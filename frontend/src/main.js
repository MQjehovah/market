import { createApp } from 'vue'
import App from './App.vue'
import router from './router'
import './styles/main.css'
import '@vue-flow/core/dist/style.css'
import '@vue-flow/core/dist/theme-default.css'

createApp(App).use(router).mount('#app')
