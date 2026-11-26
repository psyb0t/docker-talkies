const repository = "https://github.com/psyb0t/docker-talkies";
const baseUrl = "/docker-talkies/";

export default {
  title: "Talkies",
  tagline: "Speech transcription and synthesis",
  url: "https://psyb0t.github.io",
  baseUrl,
  trailingSlash: true,
  future: { faster: { rspackBundler: true, swcJsLoader: true } },
  organizationName: "psyb0t",
  projectName: "docker-talkies",
  onBrokenLinks: "throw",
  markdown: { format: "detect", hooks: { onBrokenMarkdownLinks: "throw" } },
  staticDirectories: [".assets"],
  scripts: [{ src: "https://psyb0t.github.io/analytics.js", async: true }],
  presets: [
    ["classic", {
      blog: false,
      sitemap: { ignorePatterns: [baseUrl + "search", baseUrl + "search/"] },
      theme: { customCss: "./src/css/theme.css" },
      docs: {
        path: ".content",
        routeBasePath: "/",
        sidebarPath: "./sidebars.mjs",
        editUrl: repository + "/edit/main/docs/",
      },
    }],
  ],
  themes: [
    ["@easyops-cn/docusaurus-search-local", {
      docsDir: ".content",
      docsRouteBasePath: "/",
      indexBlog: false,
      hashed: true,
    }],
  ],
  themeConfig: {
    navbar: {
      title: "Talkies",
      items: [
        { href: "https://psyb0t.github.io/projects/", label: "All projects", position: "left" },
        { href: repository, label: "GitHub", position: "right" },
        { href: repository + "/blob/main/CHANGELOG.md", label: "Changelog", position: "right" },
      ],
    },
    colorMode: { defaultMode: "dark" },
  },
};
