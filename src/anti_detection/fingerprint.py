import logging
from playwright.sync_api import Page

logger = logging.getLogger(__name__)

def spoof_fingerprints(page: Page):
    """
    Inject scripts to spoof WebGL, Canvas, navigator properties, etc.
    """
    # Override navigator.webdriver
    page.add_init_script("""
        Object.defineProperty(navigator, 'webdriver', {
            get: () => undefined
        });
    """)
    # Override chrome.runtime (for extensions detection)
    page.add_init_script("""
        window.chrome = {
            runtime: {}
        };
    """)
    # Spoof plugins and mimeTypes
    page.add_init_script("""
        const originalPlugins = navigator.plugins;
        Object.defineProperty(navigator, 'plugins', {
            get: () => {
                const plugins = [];
                for (let i = 0; i < 5; i++) {
                    plugins.push({
                        name: 'Plugin ' + i,
                        filename: 'plugin' + i + '.dll',
                        description: 'Generic plugin',
                        length: 1,
                        item: () => null,
                        namedItem: () => null
                    });
                }
                return plugins;
            }
        });
        // Also spoof mimeTypes
        Object.defineProperty(navigator, 'mimeTypes', {
            get: () => {
                const types = [];
                for (let i = 0; i < 3; i++) {
                    types.push({
                        type: 'application/x-test',
                        suffixes: 'test',
                        description: 'Test MIME'
                    });
                }
                return types;
            }
        });
    """)
    logger.info("Fingerprint spoofing scripts injected.")