import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'
import '@fontsource-variable/geist'
import './index.css'
import { AuthProvider } from './lib/auth.tsx'
import { AppRoutes } from './routes/AppRoutes.tsx'

// Restore the saved theme (or default to the dark "cockpit" hero) before paint.
const saved = localStorage.getItem('decision-ledger-theme')
if (saved === 'light') {
  document.documentElement.classList.remove('dark')
} else {
  document.documentElement.classList.add('dark')
}

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <BrowserRouter>
      <AuthProvider>
        <AppRoutes />
      </AuthProvider>
    </BrowserRouter>
  </StrictMode>,
)
