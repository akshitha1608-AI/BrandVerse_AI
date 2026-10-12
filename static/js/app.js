const menuToggle = document.querySelector('.menu-toggle');
const navigation = document.querySelector('.main-nav');

if (menuToggle && navigation) {
  menuToggle.addEventListener('click', () => {
    const isOpen = navigation.classList.toggle('is-open');
    menuToggle.setAttribute('aria-expanded', String(isOpen));
  });
}

const ambientScene = document.querySelector('.ambient-scene');
const reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)');

if (ambientScene && !reduceMotion.matches) {
  const parallaxElements = ambientScene.querySelectorAll('[data-parallax]');
  const pointerElements = ambientScene.querySelectorAll('[data-pointer-depth]');
  let scrollFrame = 0;

  const updateParallax = () => {
    const scrollY = window.scrollY;
    parallaxElements.forEach((element) => {
      const speed = Number(element.dataset.parallax);
      const offset = Math.max(-48, Math.min(48, scrollY * speed));
      element.style.setProperty('--parallax-y', `${offset.toFixed(1)}px`);
    });
    scrollFrame = 0;
  };

  window.addEventListener('scroll', () => {
    if (!scrollFrame) {
      scrollFrame = window.requestAnimationFrame(updateParallax);
    }
  }, { passive: true });

  if (pointerElements.length) {
    let pointerFrame = 0;
    let pointerX = 0;
    let pointerY = 0;

    const updatePointer = () => {
      pointerElements.forEach((element) => {
        const depth = Number(element.dataset.pointerDepth);
        element.style.setProperty('--pointer-shift-x', `${(pointerX * depth * 22).toFixed(1)}px`);
        element.style.setProperty('--pointer-shift-y', `${(pointerY * depth * 16).toFixed(1)}px`);
      });
      pointerFrame = 0;
    };

    window.addEventListener('pointermove', (event) => {
      if (event.pointerType === 'touch') return;
      pointerX = (event.clientX / window.innerWidth - 0.5) * 2;
      pointerY = (event.clientY / window.innerHeight - 0.5) * 2;
      if (!pointerFrame) {
        pointerFrame = window.requestAnimationFrame(updatePointer);
      }
    }, { passive: true });
  }
}

document.querySelectorAll('[data-project-upload]').forEach((input) => {
  const previewGrid = input.closest('form')?.querySelector('[data-upload-previews]');
  if (!previewGrid) return;
  const previewUrls = [];

  input.addEventListener('change', () => {
    previewUrls.forEach((url) => URL.revokeObjectURL(url));
    previewUrls.length = 0;
    previewGrid.replaceChildren();
    const files = Array.from(input.files || []);
    const form = input.closest('form');
    const maxFiles = Number(form.dataset.maxFiles || 12);
    const maxRequest = Number(form.dataset.maxRequestBytes) || Number.POSITIVE_INFINITY;
    const maxImage = Number(form.dataset.maxImageBytes) || 12 * 1024 * 1024;
    const maxVideo = Number(form.dataset.maxVideoBytes) || 80 * 1024 * 1024;
    const total = files.reduce((sum, file) => sum + file.size, 0);
    const isImage = (file) => file.type.startsWith('image/') || /\.(jpe?g|png|webp)$/i.test(file.name);
    const isVideo = (file) => file.type.startsWith('video/') || /\.(mp4|webm)$/i.test(file.name);
    const error = files.length > maxFiles
      ? `Choose no more than ${maxFiles} files.`
      : total > maxRequest - 1024 * 1024
        ? 'Combined files exceed the request upload limit.'
        : files.find((file) => isImage(file) ? file.size > maxImage : isVideo(file) ? file.size > maxVideo : true)
          ? 'Choose supported JPG, PNG, WebP, MP4, or WebM files within the displayed size limits.'
          : '';

    if (error) {
      input.value = '';
      const message = document.createElement('p');
      message.className = 'upload-error';
      message.setAttribute('role', 'alert');
      message.textContent = error;
      previewGrid.append(message);
      return;
    }

    files.forEach((file) => {
      const card = document.createElement('div');
      card.className = 'upload-preview-card';
      const preview = isImage(file) ? document.createElement('img') : document.createElement('video');
      const previewUrl = URL.createObjectURL(file);
      previewUrls.push(previewUrl);
      preview.src = previewUrl;
      preview.alt = isImage(file) ? file.name : '';
      if (preview instanceof HTMLVideoElement) {
        preview.muted = true;
        preview.playsInline = true;
        preview.preload = 'metadata';
      }
      const details = document.createElement('span');
      details.className = 'upload-preview-name';
      details.textContent = `${file.name} · ${(file.size / (1024 * 1024)).toFixed(1)} MB`;
      const label = document.createElement('label');
      label.className = 'media-caption-label';
      label.textContent = 'Caption (optional)';
      const caption = document.createElement('input');
      caption.name = 'new_media_caption';
      caption.maxLength = 240;
      caption.placeholder = 'Describe this image or video';
      caption.setAttribute('aria-label', `Caption for ${file.name}`);
      label.append(caption);
      card.append(preview, details, label);
      previewGrid.append(card);
    });
  });
});

const lightbox = document.querySelector('[data-media-lightbox]');
const lightboxImage = lightbox?.querySelector('[data-lightbox-image]');
if (lightbox && lightboxImage) {
  document.querySelectorAll('[data-lightbox-src]').forEach((trigger) => {
    trigger.addEventListener('click', () => {
      lightboxImage.src = trigger.dataset.lightboxSrc;
      lightboxImage.alt = trigger.dataset.lightboxAlt || '';
      lightbox.showModal();
    });
  });
  lightbox.querySelector('[data-lightbox-close]')?.addEventListener('click', () => lightbox.close());
  lightbox.addEventListener('click', (event) => {
    if (event.target === lightbox) lightbox.close();
  });
  lightbox.addEventListener('close', () => {
    lightboxImage.removeAttribute('src');
    lightboxImage.alt = '';
  });
}

document.querySelectorAll('[data-copy-share]').forEach((button) => {
  button.addEventListener('click', async () => {
    const feedback = button.parentElement.querySelector('[data-share-feedback]');
    const body = new URLSearchParams({ csrf_token: button.dataset.csrfToken || '' });
    try {
      if (navigator.clipboard && window.isSecureContext) {
        await navigator.clipboard.writeText(window.location.href);
        if (feedback) feedback.textContent = 'Project link copied.';
      } else if (feedback) {
        feedback.textContent = 'Copy the project address from your browser bar.';
      }
      const response = await fetch(button.dataset.shareEndpoint, {
        method: 'POST',
        credentials: 'same-origin',
        headers: { 'Content-Type': 'application/x-www-form-urlencoded;charset=UTF-8' },
        body,
      });
      if (!response.ok) throw new Error(`Share analytics returned ${response.status}`);
    } catch (error) {
      if (feedback) feedback.textContent = 'The project link is available in your browser address bar.';
      console.error('Unable to copy the project link or record its share.', error);
    }
  });
});

document.querySelectorAll('[data-project-video]').forEach((video) => {
  const sent = new Set();
  const sendEvent = (eventType) => {
    if (sent.has(eventType)) return;
    sent.add(eventType);
    const body = new URLSearchParams({
      event_type: eventType,
      csrf_token: video.dataset.csrfToken || '',
    });
    fetch(video.dataset.analyticsUrl, {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/x-www-form-urlencoded;charset=UTF-8' },
      body,
    }).then((response) => {
      if (!response.ok) throw new Error(`Video analytics returned ${response.status}`);
    }).catch((error) => console.error('Unable to record project video analytics.', error));
  };
  video.addEventListener('play', () => sendEvent('video_start'), { once: true });
  video.addEventListener('timeupdate', () => {
    if (video.duration > 0 && video.currentTime / video.duration >= 0.9) {
      sendEvent('video_complete');
    }
  });
});
