/**
 * Navigation.js
 * 
 * Handles SPA-like navigation to keep the player running.
 * Intercepts clicks on internal links, fetches the new page,
 * and swaps the content of the <main> element.
 */
export class Navigation {
    constructor() {
        this.mainContent = document.querySelector('main.content');
        this.bindEvents();

        // Handle back/forward buttons
        window.addEventListener('popstate', (event) => {
            if (event.state && event.state.path) {
                this.loadPage(event.state.path, false);
            } else {
                // Fallback for initial state or external navigation
                this.loadPage(window.location.pathname, false);
            }
        });
    }

    bindEvents() {
        document.body.addEventListener('click', (e) => {
            const link = e.target.closest('a');
            if (link) {
                const href = link.getAttribute('href');
                // Check if it's an internal link and not a hash link
                if (href && href.startsWith('/') && !href.startsWith('#')) {
                    e.preventDefault();
                    this.navigateTo(href);
                }
            }
        });
    }

    async navigateTo(url) {
        await this.loadPage(url, true);
    }

    async loadPage(url, pushState = true) {
        try {
            console.log(`Navigating to: ${url}`);

            // Add loading state if desired
            this.mainContent.style.opacity = '0.5';

            const response = await fetch(url);
            if (!response.ok) throw new Error(`HTTP error! status: ${response.status}`);

            const text = await response.text();
            const parser = new DOMParser();
            const doc = parser.parseFromString(text, 'text/html');

            const newContent = doc.querySelector('main.content');
            if (!newContent) throw new Error('No main content found in response');

            // Swap content
            this.mainContent.innerHTML = newContent.innerHTML;

            // Update document title
            document.title = doc.title;

            // Update URL history
            if (pushState) {
                window.history.pushState({ path: url }, doc.title, url);
            }

            // Trigger custom event for App.js to re-bind listeners
            const event = new CustomEvent('page:loaded', {
                detail: { url: url }
            });
            document.dispatchEvent(event);

        } catch (error) {
            console.error('Navigation failed:', error);
            // Fallback to full reload if SPA nav fails
            if (pushState) window.location.href = url;
        } finally {
            this.mainContent.style.opacity = '1';
        }
    }
}
