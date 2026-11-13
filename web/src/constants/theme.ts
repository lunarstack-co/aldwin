/** sRGB of `--background` in tokens.css, for the theme-color meta tag. */
export const THEME_COLORS = {
  light: "#fcfcfb",
  dark: "#0f0f0e",
} as const;

/** next-themes keeps the pick in this browser only. */
export const THEMES = ["light", "dark", "system"] as const;
