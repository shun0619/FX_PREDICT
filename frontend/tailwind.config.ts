import type { Config } from "tailwindcss";

const config: Config = {
  content: ["./app/**/*.{ts,tsx}", "./components/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        bg: "#0b0e14",
        panel: "#141821",
        panel2: "#1a1f2b",
        line: "#232a38",
        accent: "#3b82f6",
        up: "#22c55e",
        down: "#ef4444",
        muted: "#8b93a7",
      },
    },
  },
  plugins: [],
};
export default config;
