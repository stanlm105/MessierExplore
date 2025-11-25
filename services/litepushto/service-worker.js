self.addEventListener('install', (e)=>{
  e.waitUntil(caches.open('litepushto-v17').then(c=>c.addAll([
    '/', '/index.html', '/styles.css', '/app.js', '/manifest.webmanifest'
  ])));
});
self.addEventListener('fetch', (e)=>{
  e.respondWith(caches.match(e.request).then(r=> r || fetch(e.request)));
});
