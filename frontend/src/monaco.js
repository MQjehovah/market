import { ensureMonacoEnv } from './monaco-env.js'
import * as monaco from 'monaco-editor'
import { json } from 'monaco-editor'

ensureMonacoEnv()
json.jsonDefaults.setDiagnosticsOptions({
  validate: true,
  allowComments: false,
  schemaValidation: 'warning',
  comments: 'error',
  trailingCommas: 'error'
})

export default monaco
