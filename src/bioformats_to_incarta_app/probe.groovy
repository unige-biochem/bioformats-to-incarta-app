/*
 * Says which Java runs the converter, and which of the given reader classes
 * Bio-Formats actually instantiates. launcher.prepare() runs it with the same
 * class path as a conversion, so "loaded" here means a conversion uses it.
 *
 *   probe.groovy READER_CLASS...
 *
 *   B2I_JAVA <version> (<vendor>)
 *   B2I_READER <class> loaded|missing      one per argument
 */

import loci.common.DebugTools
import loci.formats.ImageReader

DebugTools.setRootLevel('WARN')
println "B2I_JAVA ${System.getProperty('java.version')} (${System.getProperty('java.vendor')})"
def loaded = new ImageReader().readers*.class*.name as Set
args.each { println "B2I_READER ${it} ${loaded.contains(it) ? 'loaded' : 'missing'}" }
