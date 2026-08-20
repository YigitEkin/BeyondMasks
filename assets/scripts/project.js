const copyButton = document.querySelector('#copy-citation');
const citation = document.querySelector('#bibtex');

if (copyButton && citation) {
  copyButton.addEventListener('click', async () => {
    try {
      await navigator.clipboard.writeText(citation.textContent);
      copyButton.textContent = 'Copied';
      window.setTimeout(() => { copyButton.textContent = 'Copy'; }, 1600);
    } catch (_) {
      copyButton.textContent = 'Select text to copy';
    }
  });
}

const supplementaryRoot = 'assets/videos';
const benchmarkGroups = [
  { label: 'Shadow', ids: ['5', '37', '48'] },
  { label: 'Reflection', ids: ['6', '59', '18'] },
  { label: 'Light source', ids: ['136', '133', '132'] },
  { label: 'Steam', ids: ['16', '137', '134'] },
  { label: 'Translucent', ids: ['25', '97', '84'] },
  { label: 'Causal physical effects', ids: ['21', '89', '72'] },
  { label: 'Fast Motion Scenes', ids: ['50', '65', '73'] },
  { label: 'No after-effect', ids: ['49', '52', '53'] },
  { label: 'Real-world videos', ids: ['117', '112', '113'] },
];

function createLazyVideo(kind, id, className) {
  const video = document.createElement('video');
  video.className = className;
  video.dataset.src = `${supplementaryRoot}/${kind}/${id}.mp4`;
  video.muted = true;
  video.loop = true;
  video.playsInline = true;
  video.preload = 'none';
  return video;
}

function loadVideo(video) {
  if (video.dataset.loaded === 'true') return;
  video.src = video.dataset.src;
  video.dataset.loaded = 'true';
  video.load();
}

function syncPlayback(videos) {
  const seekAll = (time) => {
    videos.forEach((video) => {
      if (Math.abs(video.currentTime - time) > 0.12) {
        try { video.currentTime = time; } catch (_) {}
      }
    });
  };
  videos[0].addEventListener('timeupdate', () => seekAll(videos[0].currentTime));
  videos.forEach((video) => video.addEventListener('loadedmetadata', () => seekAll(0)));
}

const videoObserver = new IntersectionObserver((entries) => {
  entries.forEach((entry) => {
    const videos = entry.target.querySelectorAll('video');
    if (entry.isIntersecting) {
      videos.forEach((video) => {
        loadVideo(video);
        video.play().catch(() => {});
      });
    } else {
      videos.forEach((video) => video.pause());
    }
  });
}, { rootMargin: '120px 0px', threshold: 0.15 });

function createBenchmarkCard(id) {
  const card = document.createElement('article');
  card.className = 'benchmark-video-card';
  card.style.setProperty('--slider-position', '50%');

  const stage = document.createElement('div');
  stage.className = 'comparison-stage';
  const inputLayer = document.createElement('div');
  inputLayer.className = 'comparison-layer input-layer';
  const inputVideo = createLazyVideo('fg_mask_clip', id, 'comparison-video');
  inputLayer.append(inputVideo);

  const referenceLayer = document.createElement('div');
  referenceLayer.className = 'comparison-layer reference-layer';
  const referenceVideo = createLazyVideo('bg_clip', id, 'comparison-video');
  referenceLayer.append(referenceVideo);

  const divider = document.createElement('div');
  divider.className = 'comparison-divider';
  const handle = document.createElement('div');
  handle.className = 'comparison-handle';
  handle.innerHTML = '<span></span>';
  const range = document.createElement('input');
  range.className = 'comparison-range';
  range.type = 'range';
  range.min = '0';
  range.max = '100';
  range.value = '50';
  range.setAttribute('aria-label', `Compare foreground and clean reference for video ${id}`);
  range.addEventListener('input', () => card.style.setProperty('--slider-position', `${range.value}%`));

  stage.append(inputLayer, referenceLayer, divider, handle, range);
  card.append(stage);

  syncPlayback([referenceVideo, inputVideo]);
  videoObserver.observe(card);
  return card;
}

function createBenchmarkGroup(group, index) {
  const section = document.createElement('section');
  section.className = 'benchmark-video-group';
  section.id = `benchmark-group-${index}`;
  section.setAttribute('role', 'tabpanel');
  section.hidden = index !== 0;
  const heading = document.createElement('div');
  heading.className = 'benchmark-group-heading';
  heading.innerHTML = `<div><span>After-effect type</span><h3>${group.label}</h3></div><small>${String(index + 1).padStart(2, '0')} / ${String(benchmarkGroups.length).padStart(2, '0')}</small>`;
  const grid = document.createElement('div');
  grid.className = 'benchmark-video-grid';
  group.ids.forEach((id) => grid.appendChild(createBenchmarkCard(id)));
  section.append(heading, grid);
  return section;
}

const benchmarkVideoContainer = document.querySelector('#benchmark-video-groups');
const benchmarkCategoryTabs = document.querySelector('#benchmark-category-tabs');

function activateBenchmarkGroup(activeIndex) {
  document.querySelectorAll('.benchmark-video-group').forEach((group, index) => {
    group.hidden = index !== activeIndex;
  });
  benchmarkCategoryTabs.querySelectorAll('button').forEach((button, index) => {
    const active = index === activeIndex;
    button.classList.toggle('active', active);
    button.setAttribute('aria-selected', String(active));
    button.tabIndex = active ? 0 : -1;
  });
}

if (benchmarkVideoContainer && benchmarkCategoryTabs) {
  benchmarkGroups.forEach((group, index) => {
    benchmarkVideoContainer.appendChild(createBenchmarkGroup(group, index));
    const button = document.createElement('button');
    button.type = 'button';
    button.setAttribute('role', 'tab');
    button.setAttribute('aria-controls', `benchmark-group-${index}`);
    button.setAttribute('aria-selected', String(index === 0));
    button.className = index === 0 ? 'active' : '';
    button.tabIndex = index === 0 ? 0 : -1;
    button.textContent = group.label;
    button.addEventListener('click', () => activateBenchmarkGroup(index));
    button.addEventListener('keydown', (event) => {
      if (!['ArrowLeft', 'ArrowRight'].includes(event.key)) return;
      event.preventDefault();
      const offset = event.key === 'ArrowRight' ? 1 : -1;
      const nextIndex = (index + offset + benchmarkGroups.length) % benchmarkGroups.length;
      activateBenchmarkGroup(nextIndex);
      benchmarkCategoryTabs.querySelectorAll('button')[nextIndex].focus();
    });
    benchmarkCategoryTabs.appendChild(button);
  });
}
