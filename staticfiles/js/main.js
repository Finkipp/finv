document.addEventListener("DOMContentLoaded", function () {
    // Sidebar toggle
    const toggleBtn = document.getElementById("sidebar-toggle");
    const sidebar = document.getElementById("sidebar");
    const mainContent = document.querySelector(".main-content");

    if (toggleBtn && sidebar) {
        toggleBtn.addEventListener("click", function () {
            sidebar.classList.toggle("collapsed");
            if (mainContent) mainContent.classList.toggle("expanded");
        });
    }

    // Theme toggle
    const themeBtn = document.getElementById("theme-toggle");
    if (themeBtn) {
        themeBtn.addEventListener("click", function () {
            const html = document.documentElement;
            const current = html.getAttribute("data-theme");
            const next = current === "dark" ? "light" : "dark";
            html.setAttribute("data-theme", next);
            localStorage.setItem("finv-theme", next);
        });

        const saved = localStorage.getItem("finv-theme");
        if (saved) {
            document.documentElement.setAttribute("data-theme", saved);
        }
    }

    // Auto-search with debounce
    const searchInput = document.querySelector("[data-search]");
    if (searchInput) {
        let timer;
        searchInput.addEventListener("input", function () {
            clearTimeout(timer);
            timer = setTimeout(function () {
                const form = searchInput.closest("form");
                if (form) form.submit();
            }, 400);
        });
    }

    // Generate inventory number via AJAX
    const genBtn = document.getElementById("generate-inv");
    if (genBtn) {
        genBtn.addEventListener("click", function () {
            fetch("/equipment/generate-inv/")
                .then(function (r) { return r.json(); })
                .then(function (data) {
                    const input = document.getElementById("id_inventory_number");
                    if (input) input.value = data.inventory_number;
                });
        });
    }

    // Highlight active nav item
    const navLinks = document.querySelectorAll(".sidebar-nav a");
    const currentPath = window.location.pathname;
    navLinks.forEach(function (link) {
        const href = link.getAttribute("href");
        if (href && currentPath.startsWith(href) && href !== "/") {
            link.classList.add("active");
        } else if (href === "/" && currentPath === "/") {
            link.classList.add("active");
        }
    });
});
