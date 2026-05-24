/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{js,ts,jsx,tsx}'],
  darkMode: 'class',
  theme: {
    extend: {
      colors: {
        bg:       '#0f1117',
        surface:  '#161b22',
        surface2: '#1c2128',
        border:   '#30363d',
        text1:    '#e6edf3',
        text2:    '#8b949e',
        text3:    '#484f58',
        blue:     '#388bfd',
        green:    '#3fb950',
        yellow:   '#d29922',
        red:      '#f85149',
        purple:   '#bc8cff',
      },
      fontFamily: {
        sans: ['Inter', 'system-ui', 'sans-serif'],
        mono: ['JetBrains Mono', 'monospace'],
      },
      borderRadius: {
        card: '12px',
        btn:  '8px',
      },
    },
  },
  plugins: [],
}
