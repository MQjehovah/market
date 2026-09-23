import editorWorker from 'monaco-editor/editor/editor.worker.js?worker'
import jsonWorker from 'monaco-editor/language/json/json.worker.js?worker'

let ready = false

export function ensureMonacoEnv() {
  if (ready) return
  ready = true
  self.MonacoEnvironment = {
    getWorker(_, label) {
      if (label === 'json') return new jsonWorker()
      return new editorWorker()
    }
  }
}
