// Loaded ONLY for the compatibility profile, before any application script.
require('core-js/stable');
require('abortcontroller-polyfill/dist/abortcontroller-polyfill-only');
var transport = require('whatwg-fetch');
// Chrome 49 fetch omits same-origin cookies by default and cannot abort. This
// XHR-backed implementation preserves credentials and actually cancels requests.
window.fetch = transport.fetch;
window.Headers = transport.Headers;
window.Request = transport.Request;
window.Response = transport.Response;
require('formdata-polyfill');
require('./dom');
require('./pointer')(require('pepjs').dispatcher);
window.RIST_BROWSER_PROFILE = 'chrome49';
document.documentElement.className += ' rist-chrome49';
