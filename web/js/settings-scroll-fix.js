// Enhanced Settings Scroll Fix
// This can be added to your loadSettingsPage function after the HTML is rendered

function setupSettingsScrolling(content) {
    const nav = content.querySelector('.settings-nav');
    if (!nav) return;

    const links = Array.from(nav.querySelectorAll('a'));
    const sections = Array.from(content.querySelectorAll('.settings-panel'));

    // Helper to set active link
    function setActiveLink(targetId) {
        links.forEach(a => {
            a.classList.toggle('active', a.getAttribute('href') === `#${targetId}`);
        });
        // Optionally scroll the active link into view in the nav
        const active = nav.querySelector('a.active');
        if (active) {
            active.scrollIntoView({ block: 'nearest', inline: 'nearest', behavior: 'smooth' });
        }
    }

    // Click handler for smooth scrolling
    links.forEach(a => {
        a.addEventListener('click', (e) => {
            e.preventDefault();
            const id = a.getAttribute('href').slice(1);
            const section = content.querySelector(`#${id}`);
            if (!section) return;

            // Get the actual positions using getBoundingClientRect
            const containerRect = content.getBoundingClientRect();
            const sectionRect = section.getBoundingClientRect();

            // Calculate the scroll distance
            // sectionRect.top - containerRect.top gives us the relative position
            // Add content.scrollTop to get the absolute scroll position
            // Subtract 30 for some top padding
            const scrollDistance = sectionRect.top - containerRect.top + content.scrollTop - 30;

            // Smooth scroll to the calculated position
            content.scrollTo({
                top: scrollDistance,
                behavior: 'smooth'
            });

            // Update active state
            setActiveLink(id);

            // Update URL hash
            history.replaceState(null, '', `#settings#${id}`);
        });
    });

    // Track active section on scroll
    let scrollTimeout;
    function updateActiveOnScroll() {
        const containerRect = content.getBoundingClientRect();
        let activeSection = null;
        let minDistance = Infinity;

        sections.forEach(section => {
            const sectionRect = section.getBoundingClientRect();
            const distanceFromTop = Math.abs(sectionRect.top - containerRect.top);

            // Consider a section active if it's near the top of the viewport
            if (distanceFromTop < minDistance && sectionRect.top - containerRect.top < 100) {
                minDistance = distanceFromTop;
                activeSection = section;
            }
        });

        if (activeSection) {
            setActiveLink(activeSection.id);
        }
    }

    // Debounced scroll handler
    content.addEventListener('scroll', () => {
        clearTimeout(scrollTimeout);
        scrollTimeout = setTimeout(updateActiveOnScroll, 50);
    }, { passive: true });

    // Set initial active state
    const initialHash = location.hash.split('#').pop() || 'perf';
    setActiveLink(initialHash);

    // Scroll to initial section if hash present
    if (location.hash && location.hash.includes('settings')) {
        const targetId = location.hash.split('#').pop();
        const targetSection = content.querySelector(`#${targetId}`);
        if (targetSection) {
            setTimeout(() => {
                const containerRect = content.getBoundingClientRect();
                const sectionRect = targetSection.getBoundingClientRect();
                const scrollDistance = sectionRect.top - containerRect.top + content.scrollTop - 30;
                content.scrollTo({
                    top: scrollDistance,
                    behavior: 'auto'
                });
            }, 100);
        }
    }
}

// Export for use in app.js
if (typeof module !== 'undefined' && module.exports) {
    module.exports = setupSettingsScrolling;
}