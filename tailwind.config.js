/** @type {import('tailwindcss').Config} */
module.exports = {
  darkMode: 'class',
  content: ['./index.html', './js/**/*.js'],
  theme: {
    extend: {
      colors: {
        cyberDark: '#060913',
        cyberCard: 'rgba(13, 20, 38, 0.75)',
        cyberBorder: 'rgba(43, 62, 107, 0.45)',
        cyberCyan: '#00f0ff',
        cyberBlue: '#2563eb',
        cyberPurple: '#a855f7'
      }
    }
  },
  plugins: []
};
