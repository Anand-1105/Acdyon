// Acdyon Frontend Configuration
// When running locally on localhost/127.0.0.1, uses same-origin relative paths ("").
// When deployed on Cloudflare Pages, automatically targets the live production Render backend.
window.PUBLIC_API_BASE_URL = (
  window.location.hostname === "localhost" ||
  window.location.hostname === "127.0.0.1"
) ? "" : "https://acdyon-backend-72ph.onrender.com";
