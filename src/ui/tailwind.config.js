// Tradleware/src/ui/tailwind.config.js
/** @type {import('tailwindcss').Config} */
module.exports = {
  content: [
    "./templates/**/*.html",    // Scan all HTML files in src/ui/templates/
    "./static/**/*.{css,js}",   // Scan static CSS/JS files within src/ui/static/
    "../**/*.py",               // Scan ALL Python files within src/ (e.g., src/misc, src/traders)
    "../../tradleware.py",      // Scan tradleware.py at the project root
  ],
  theme: {
    extend: {},
  },
  plugins: [],
}