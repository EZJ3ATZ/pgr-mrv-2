import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { ModuleRegistry, AllCommunityModule } from 'ag-grid-community'
import './index.css'
import App from './App.jsx'

// Registrar módulos AG Grid uma única vez no entry point
ModuleRegistry.registerModules([AllCommunityModule])

createRoot(document.getElementById('root')).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
