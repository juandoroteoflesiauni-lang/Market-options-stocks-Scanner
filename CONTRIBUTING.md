# Guía de Contribución

¡Gracias por tu interés en contribuir al escáner de opciones y mercado!

## Estructura del Proyecto
- `src/quant_engine/`: Motor matemático y predictivo.
- `backend/`: API y manejo de datos asíncronos.
- `frontend/`: Interfaz de usuario.

## Entorno de Desarrollo Local

### 1. Backend (Python)
Requerimos Python 3.12+. Para levantar el motor:
\`\`\`bash
python -m venv .venv
source .venv/bin/activate  # o .venv\Scripts\activate en Windows
pip install -r requirements.txt
\`\`\`
*Nota para módulos de alto rendimiento: Si trabajas en los módulos acelerados por GPU, asegúrate de tener el entorno CUDA configurado.*

### 2. Frontend (Next.js)
\`\`\`bash
cd frontend
npm install
npm run dev
\`\`\`

## Estándares de Código
- **Backend:** Usamos PEP 8. Asegúrate de ejecutar `pytest` y que las pruebas pasen antes de enviar un PR.
- **Frontend:** Usamos React 19 y Tailwind 4. El sistema de diseño exige mantener la directiva de UI: estética 100% modo oscuro con estilo *glassmorphism*. Por favor, no introduzcas variables de modo claro en `globals.css`.

## Proceso de Pull Requests
1. Haz un fork del repositorio.
2. Crea tu rama de características (`git checkout -b feature/AmazingFeature`).
3. Haz un commit con tus cambios (`git commit -m 'Add some AmazingFeature'`).
4. Haz push a la rama (`git push origin feature/AmazingFeature`).
5. Abre un Pull Request usando el template del repositorio.
