/* Native adaptations of 21st.dev-listed Motion Primitives patterns.
 * Sources and MIT notices: THIRD_PARTY_NOTICES.md.
 * Content, links and disclosures remain usable without this enhancement. */
const reducedMotion = matchMedia('(prefers-reduced-motion: reduce)');

// Animated Background: one decorative highlight shared by pointer/keyboard targets.
const nav = document.querySelector('[data-animated-nav]');
if (nav) {
  const highlight = document.createElement('span');
  highlight.className = 'nav-highlight';
  highlight.setAttribute('aria-hidden', 'true');
  nav.prepend(highlight);
  let activeLink = null;
  let previousBox = null;
  let animation;
  const show = (link) => {
    if (!link || !nav.contains(link)) { clear(); return; }
    const box = { x: link.offsetLeft, y: link.offsetTop, width: link.offsetWidth, height: link.offsetHeight };
    animation?.cancel();
    highlight.style.width = `${box.width}px`;
    highlight.style.height = `${box.height}px`;
    highlight.style.transform = `translate(${box.x}px, ${box.y}px)`;
    highlight.style.opacity = '1';
    if (previousBox && !reducedMotion.matches) {
      animation = highlight.animate([
        { transform: `translate(${previousBox.x}px, ${previousBox.y}px) scaleX(${previousBox.width / box.width}) scaleY(${previousBox.height / box.height})` },
        { transform: `translate(${box.x}px, ${box.y}px) scale(1)` },
      ], { duration: 240, easing: 'cubic-bezier(.22,1,.36,1)' });
    }
    activeLink = link;
    previousBox = box;
  };
  const clear = () => {
    animation?.cancel();
    highlight.style.opacity = '0';
    activeLink = previousBox = null;
  };
  nav.addEventListener('pointerover', (event) => {
    if (event.pointerType !== 'touch') show(event.target.closest('a:not(.button)'));
  });
  nav.addEventListener('pointerleave', () => {
    const focused = nav.querySelector('a:focus-visible');
    focused ? show(focused) : clear();
  });
  nav.addEventListener('focusin', (event) => show(event.target.closest('a:not(.button)')));
  nav.addEventListener('focusout', (event) => {
    if (!event.relatedTarget || !nav.contains(event.relatedTarget)) clear();
  });
  new ResizeObserver(() => { if (activeLink) { previousBox = null; show(activeLink); } }).observe(nav);
  reducedMotion.addEventListener('change', () => animation?.cancel());
}

// Morphing Dialog image pattern, using native modal semantics and focus containment.
const dialog = document.querySelector('.photo-dialog');
const photos = [...document.querySelectorAll('[data-photo]')];
if (dialog && typeof dialog.showModal === 'function' && photos.length) {
  const image = dialog.querySelector('.expanded-photo');
  const caption = dialog.querySelector('#photo-caption');
  const counter = dialog.querySelector('.photo-counter');
  const close = dialog.querySelector('.dialog-close');
  let index = 0;
  let opener;
  let entryAnimation;
  let savedOverflow;
  const render = () => {
    const original = photos[index].querySelector('img');
    image.src = photos[index].href;
    image.alt = original.alt;
    // Use the complete existing descriptive alt text in the expanded view.
    caption.textContent = original.alt;
    counter.textContent = `Photo ${index + 1} of ${photos.length}`;
  };
  photos.forEach((link, position) => {
    link.setAttribute('aria-haspopup', 'dialog');
    link.setAttribute('aria-label', `Enlarge photo: ${link.querySelector('img').alt}`);
    link.addEventListener('click', (event) => {
      // Preserve normal modified-click/open-in-new-tab behavior.
      if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
      event.preventDefault();
      opener = link;
      index = position;
      render();
      savedOverflow = document.body.style.overflow;
      document.body.style.overflow = 'hidden';
      dialog.showModal();
      close.focus({ preventScroll: true });
      if (!reducedMotion.matches) {
        entryAnimation = dialog.animate([{ opacity: 0, transform: 'scale(.96)' }, { opacity: 1, transform: 'scale(1)' }], { duration: 220, easing: 'cubic-bezier(.22,1,.36,1)' });
      }
    });
  });
  const step = (direction) => { index = (index + direction + photos.length) % photos.length; render(); };
  close.addEventListener('click', () => dialog.close());
  dialog.querySelector('[data-photo-prev]').addEventListener('click', () => step(-1));
  dialog.querySelector('[data-photo-next]').addEventListener('click', () => step(1));
  dialog.addEventListener('keydown', (event) => {
    if (event.key === 'ArrowLeft' || event.key === 'ArrowRight') {
      event.preventDefault();
      step(event.key === 'ArrowLeft' ? -1 : 1);
    }
  });
  // Only clicks that start and end on the backdrop dismiss the modal.
  let backdropPress = false;
  dialog.addEventListener('pointerdown', (event) => { backdropPress = event.target === dialog; });
  dialog.addEventListener('click', (event) => {
    if (backdropPress && event.target === dialog) dialog.close();
    backdropPress = false;
  });
  dialog.addEventListener('close', () => {
    entryAnimation?.cancel();
    document.body.style.overflow = savedOverflow;
    opener?.focus({ preventScroll: true });
  });
  reducedMotion.addEventListener('change', () => entryAnimation?.cancel());
}
