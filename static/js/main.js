document.addEventListener("DOMContentLoaded", function () {
    function readPreference(key) {
        try {
            return localStorage.getItem(key);
        } catch (error) {
            return null;
        }
    }

    function savePreference(key, value) {
        try {
            localStorage.setItem(key, value);
        } catch (error) {
            // The interface still works when storage is blocked by the browser.
        }
    }

    const sidebar = document.getElementById("sidebar");
    const toggleBtn = document.getElementById("sidebar-toggle");
    const mainContent = document.querySelector(".main-content");
    const sidebarBackdrop = document.getElementById("sidebar-backdrop");
    const mobileSidebar = window.matchMedia("(max-width: 768px)");

    function setSidebarCollapsed(collapsed) {
        if (!sidebar) return;
        sidebar.classList.toggle("collapsed", collapsed);
        if (mainContent) mainContent.classList.toggle("expanded", collapsed);
        if (toggleBtn) toggleBtn.setAttribute("aria-expanded", String(!collapsed));
        if (sidebarBackdrop) {
            sidebarBackdrop.classList.toggle("visible", mobileSidebar.matches && !collapsed);
        }
    }

    const savedSidebarState = readPreference("finv-sidebar-collapsed");
    if (mobileSidebar.matches) {
        setSidebarCollapsed(true);
    } else if (savedSidebarState !== null) {
        setSidebarCollapsed(savedSidebarState === "true");
    }

    if (toggleBtn && sidebar) {
        toggleBtn.addEventListener("click", function () {
            const collapsed = !sidebar.classList.contains("collapsed");
            setSidebarCollapsed(collapsed);
            if (!mobileSidebar.matches) {
                savePreference("finv-sidebar-collapsed", String(collapsed));
            }
        });
    }

    if (sidebarBackdrop) {
        sidebarBackdrop.addEventListener("click", function () {
            setSidebarCollapsed(true);
        });
    }

    mobileSidebar.addEventListener("change", function (event) {
        if (event.matches) {
            setSidebarCollapsed(true);
        } else {
            setSidebarCollapsed(readPreference("finv-sidebar-collapsed") === "true");
        }
    });

    const referencesMenu = document.getElementById("references-menu");
    if (referencesMenu) {
        const savedReferencesState = readPreference("finv-references-open");
        if (savedReferencesState !== null) {
            referencesMenu.open = savedReferencesState === "true";
        }
        referencesMenu.addEventListener("toggle", function () {
            savePreference("finv-references-open", String(referencesMenu.open));
        });
        const summary = referencesMenu.querySelector("summary");
        if (summary) {
            summary.addEventListener("click", function (event) {
                if (sidebar && sidebar.classList.contains("collapsed")) {
                    event.preventDefault();
                    setSidebarCollapsed(false);
                    referencesMenu.open = true;
                    if (!mobileSidebar.matches) {
                        savePreference("finv-sidebar-collapsed", "false");
                    }
                }
            });
        }
    }

    document.querySelectorAll(".folder-group[data-folder-key]").forEach(function (folder) {
        const key = "finv-consumable-folder-" + folder.dataset.folderKey;
        const savedFolderState = readPreference(key);
        if (savedFolderState !== null) {
            folder.open = savedFolderState === "true";
        }
        folder.addEventListener("toggle", function () {
            savePreference(key, String(folder.open));
        });
    });

    const themeBtn = document.getElementById("theme-toggle");
    const savedTheme = readPreference("finv-theme");
    if (savedTheme === "light" || savedTheme === "dark") {
        document.documentElement.setAttribute("data-theme", savedTheme);
    }
    if (themeBtn) {
        themeBtn.addEventListener("click", function () {
            const html = document.documentElement;
            const current = html.getAttribute("data-theme");
            const next = current === "dark" ? "light" : "dark";
            html.setAttribute("data-theme", next);
            savePreference("finv-theme", next);
        });
    }

    const filterToggle = document.getElementById("filter-toggle");
    const filterMenu = document.getElementById("filter-menu");
    if (filterToggle && filterMenu) {
        filterToggle.addEventListener("click", function (event) {
            event.stopPropagation();
            if (filterMenu.classList.contains("open")) {
                filterMenu.classList.remove("open");
            } else {
                const rect = filterToggle.getBoundingClientRect();
                filterMenu.style.top = (rect.bottom + 6) + "px";
                filterMenu.style.right = (window.innerWidth - rect.right) + "px";
                filterMenu.classList.add("open");
            }
        });
        document.addEventListener("click", function (event) {
            if (!filterToggle.contains(event.target) && !filterMenu.contains(event.target)) {
                filterMenu.classList.remove("open");
            }
        });
    }

    const genBtn = document.getElementById("generate-inv");
    if (genBtn) {
        genBtn.addEventListener("click", function () {
            fetch("/equipment/generate-inv/")
                .then(function (response) { return response.json(); })
                .then(function (data) {
                    const input = document.getElementById("id_inventory_number");
                    if (input) input.value = data.inventory_number;
                });
        });
    }

    const navLinks = document.querySelectorAll(".sidebar-nav a");
    const currentPath = window.location.pathname;
    navLinks.forEach(function (link) {
        const href = link.getAttribute("href");
        if (href && currentPath.startsWith(href) && href !== "/") {
            link.classList.add("active");
        } else if (href === "/" && currentPath === "/") {
            link.classList.add("active");
        }
        link.addEventListener("click", function () {
            if (mobileSidebar.matches) setSidebarCollapsed(true);
        });
    });

    const aboutDialog = document.getElementById("about-dialog");
    const aboutOpen = document.getElementById("about-open");
    const aboutClose = document.getElementById("about-close");
    if (aboutDialog && aboutOpen) {
        aboutOpen.addEventListener("click", function () {
            if (typeof aboutDialog.showModal === "function") {
                aboutDialog.showModal();
            } else {
                aboutDialog.setAttribute("open", "");
            }
        });
    }
    if (aboutDialog && aboutClose) {
        aboutClose.addEventListener("click", function () {
            aboutDialog.close();
        });
        aboutDialog.addEventListener("click", function (event) {
            if (event.target === aboutDialog) aboutDialog.close();
        });
    }
});
