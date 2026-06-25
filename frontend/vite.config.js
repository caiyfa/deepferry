import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { fileURLToPath, URL } from "node:url";
export default defineConfig({
    plugins: [react()],
    resolve: {
        alias: {
            "@": fileURLToPath(new URL("./src", import.meta.url)),
        },
    },
    // Tauri expects a fixed dev port so the Rust shell can point at it.
    server: {
        port: 5173,
        strictPort: true,
    },
    // Produce sourcemaps for the bundled build (Tauri loads dist/).
    build: {
        target: "es2022",
        sourcemap: true,
    },
    clearScreen: false,
});
