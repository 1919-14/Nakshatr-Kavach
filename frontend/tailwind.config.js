/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{js,jsx,ts,tsx}'],
  theme: {
    extend: {
      colors: {
        'bg-primary':    '#020817',
        'bg-secondary':  '#0A0E2B',
        'bg-card':       '#0D1B3E',
        'bg-elevated':   '#122448',
        'electric':      '#00D4FF',
        'electric-dim':  '#0099BB',
        'gold':          '#FFD700',
        'gold-dim':      '#CC9900',
        'storm-g1':      '#4CAF50',
        'storm-g2':      '#CDDC39',
        'storm-g3':      '#FF9800',
        'storm-g4':      '#F44336',
        'storm-g5':      '#9C27B0',
        'risk-critical': '#EF5350',
        'risk-high':     '#FF8F00',
        'risk-moderate': '#FDD835',
        'risk-low':      '#43A047',
        'text-primary':  '#E8F4FD',
        'text-secondary':'#90A4AE',
        'text-muted':    '#546E7A',
        'text-accent':   '#00D4FF',
      },
      fontFamily: {
        orbitron: ['Orbitron', 'sans-serif'],
        space:    ['Space Grotesk', 'sans-serif'],
        mono:     ['JetBrains Mono', 'monospace'],
      },
      animation: {
        'pulse-slow':   'pulse 3s cubic-bezier(0.4,0,0.6,1) infinite',
        'pulse-fast':   'pulse 0.8s cubic-bezier(0.4,0,0.6,1) infinite',
        'shimmer':      'shimmer 2s infinite',
        'marquee':      'marquee 30s linear infinite',
        'glow-pulse':   'glowPulse 2s ease-in-out infinite',
      },
      keyframes: {
        shimmer: {
          '0%':   { backgroundPosition: '-200% 0' },
          '100%': { backgroundPosition: '200% 0' },
        },
        marquee: {
          '0%':   { transform: 'translateX(100%)' },
          '100%': { transform: 'translateX(-100%)' },
        },
        glowPulse: {
          '0%,100%': { opacity: '0.6' },
          '50%':     { opacity: '1' },
        },
      },
    },
  },
  plugins: [],
}
