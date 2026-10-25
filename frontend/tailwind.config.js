/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        ink: "#111827",
        muted: "#5F6673",
        line: "#E5E7EB",
        control: "#D5D9E0",
        accent: "#4F46E5",
        tint: "#EEF0FF",
        sidebar: "#F7F8FA"
      },
      boxShadow: { composer: "0 1px 2px rgb(17 24 39 / 0.08)" }
    }
  },
  plugins: []
};
