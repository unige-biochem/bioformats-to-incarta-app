/*
 * Runs BioformatsToIncartaCommand headless, and narrates it on stdout.
 *
 * This is the only Groovy in the app: fiji.py launches it through jgo with
 * groovy.ui.GroovyMain as the main class, and reads back the lines below. The
 * command is run through the CommandService, so it is the same SciJava command
 * Fiji's menu runs - only the dialog is replaced by arguments.
 *
 *   run_command.groovy [--watch-stdin] INPUT OUTPUT SERIES COMPRESSION BIGTIFF OVERWRITE
 *
 * What it prints, one line each; anything else is the libraries talking:
 *
 *   B2I_PROGRESS <done> <total> <message>    from the command's Task
 *   B2I_DONE <planes written>                exit 0
 *   B2I_STOPPED <reason>                     exit 3, a partial dataset is left
 *   B2I_FAILED <message>                     exit 1
 *
 * With --watch-stdin, a line "stop" on stdin cancels the command's Task, which
 * stops the conversion before its next plane. So does stdin closing: if the
 * process that launched this one goes away, the JVM does not keep converting
 * on its own. It is opt-in because a JVM started with no stdin at all would
 * otherwise stop before it began.
 */

import java.util.concurrent.ExecutionException

import ch.unige.biochem.incarta.command.BioformatsToIncartaCommand
import loci.common.DebugTools
import org.scijava.Context
import org.scijava.command.CommandService
import org.scijava.event.EventHandler
import org.scijava.event.EventService
import org.scijava.log.LogService
import org.scijava.task.Task
import org.scijava.task.TaskService
import org.scijava.task.event.TaskEvent

/** Echoes the command's task, and holds on to it so it can be cancelled. */
class TaskWatcher {

    volatile Task task
    volatile String stopReason
    private List<Long> last = []

    @EventHandler
    void onEvent(TaskEvent event) {
        Task t = event.task
        if (task == null) task = t
        // A stop asked for before the task existed lands on its first event.
        // cancel() fires an event of its own, hence the isCanceled() guard.
        if (stopReason != null && !t.isCanceled()) {
            t.cancel(stopReason)
            return
        }
        // Every setter fires an event, so one plane is several of them. The
        // command sets the message before the value, so keying on the numbers
        // gives one line per plane, carrying the plane now being written.
        def now = [t.progressValue, t.progressMaximum]
        if (t.progressMaximum > 0 && now != last) {
            last = now
            println "B2I_PROGRESS ${now[0]} ${now[1]} ${t.statusMessage ?: ''}"
        }
    }

    synchronized void stop(String reason) {
        if (stopReason != null) return
        stopReason = reason
        Task t = task
        if (t != null && !t.isCanceled()) t.cancel(reason)
    }
}

def arguments = args as List
boolean watchStdin = arguments.remove('--watch-stdin')
if (arguments.size() != 6) {
    System.err.println 'usage: run_command.groovy [--watch-stdin] INPUT OUTPUT SERIES COMPRESSION BIGTIFF OVERWRITE'
    System.exit 2
}
def (input, output, series, compression, bigTiff, overwrite) = arguments

// Bio-Formats logs through logback, at DEBUG unless told otherwise.
DebugTools.setRootLevel('WARN')

def context = new Context(CommandService, LogService, TaskService)
def watcher = new TaskWatcher()
// The event service holds subscribers weakly; `watcher` keeps this one alive.
context.service(EventService).subscribe(watcher)

if (watchStdin) {
    Thread.startDaemon('stop-on-stdin') {
        try {
            def reader = new BufferedReader(new InputStreamReader(System.in))
            String line
            while ((line = reader.readLine()) != null) {
                if (line.trim() == 'stop') watcher.stop('Stopped on request')
            }
        }
        catch (IOException ignored) {
        }
        watcher.stop('The launching process went away')
    }
}

int code
try {
    def inputs = [
        inputFile      : new File(input),
        outputDirectory: new File(output),
        series         : series as int,
        compression    : compression,
        bigTiff        : bigTiff.toBoolean(),
        overwrite      : overwrite.toBoolean(),
    ]
    // process = false: no pre/postprocessors, so nothing is harvested from or
    // persisted to the preferences the same command uses inside Fiji.
    def module = context.service(CommandService)
        .run(BioformatsToIncartaCommand, false, inputs).get()
    if (watcher.task?.isCanceled()) {
        println "B2I_STOPPED ${watcher.task.cancelReason}"
        code = 3
    }
    else {
        println "B2I_DONE ${module.getOutput('planesWritten')}"
        code = 0
    }
}
catch (ExecutionException e) {
    // The command wraps the real reason in an IllegalStateException; the
    // module runner may wrap that again.
    Throwable reason = e.cause ?: e
    while (reason.cause != null && !(reason instanceof IllegalStateException)) {
        reason = reason.cause
    }
    println "B2I_FAILED ${reason.message}"
    code = 1
}
finally {
    context.dispose()
}
System.out.flush()
System.exit(code)
