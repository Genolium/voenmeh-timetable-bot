/*
  Данные и рендер как в слоях Figma:
  - Корень: data-name="Расписание"
  - Контейнер контента: data-name="Content"
  - Заголовок: data-name="Header"
  - Карточка дня: data-name="День недели"
  - Контент дня: data-name="Контент дня"
  - Элемент пары: data-name="Пара"
  - Индикатор первой пары: data-name="Первая пара" (ползунок времени)

  Цвета из Figma variables:
  White: #ffffff, Red: #ff2f00, Black: #000000, Gray: #323232,
  Vibe Red: #ff656f, Purple: #6555f0, Vibe Purple: #8274fb
*/

// Константы подложек (как в Figma get_code подсказке)
const ODD_BG = 'http://localhost:3845/assets/26e0fc3768ab0b05aec51d6e48e427e042cf2e38.png';
const EVEN_BG = 'http://localhost:3845/assets/5578897d82d2adf97158927df40b99e02b04278b.png';

// Пример JSON данных. Можно заменить на fetch(...) при необходимости
const timetableData = {
  weekType: 'odd', // 'odd' | 'even'
  // Список дней в порядке показа
  days: [
    {
      name: 'Понедельник',
      accent: '#ff2f00', // для нечетной — красный; для четной — фиолетовый
      firstStart: '9:00',
      lessons: [
        { title: 'ФИЗИКА (лаб)', room: '322*', time: '9:00 - 10:30' },
        { title: 'СИСТЕМНОЕ ПО', room: '325*', time: '10:50 - 12:20' },
        { title: 'ОСН.СИСТ.АН. (пр)', room: '259', time: '12:40 - 14:10' },
        { title: 'СИСТЕМНОЕ ПО (пр)', room: '265', time: '14:55 - 16:25' },
        { title: 'ТОЭ', room: '429', time: '16:45 - 18:15' },
      ],
    },
    { name: 'Вторник', accent: '#ff2f00', firstStart: '9:00', lessons: [] },
    { name: 'Среда', accent: '#ff2f00', firstStart: '9:00', lessons: [] },
    { name: 'Четверг', accent: '#ff2f00', firstStart: '9:00', lessons: [] },
    { name: 'Пятница', accent: '#ff2f00', firstStart: '9:00', lessons: [] },
    { name: 'Суббота', accent: '#ff2f00', firstStart: '9:00', lessons: [] },
  ],
  // Альтернативный набор на четную неделю (пример)
  evenDays: [
    {
      name: 'Понедельник',
      accent: '#6555f0',
      firstStart: '9:00',
      lessons: [
        { title: 'ФИЗИКА (лаб)', room: '322*', time: '9:00 - 10:30' },
        { title: 'СИСТЕМНОЕ ПО', room: '325*', time: '10:50 - 12:20' },
        { title: 'ОСН.СИСТ.АН. (пр)', room: '259', time: '12:40 - 14:10' },
        { title: 'СИСТЕМНОЕ ПО (пр)', room: '265', time: '14:55 - 16:25' },
        { title: 'ТОЭ', room: '429', time: '16:45 - 18:15' },
      ],
    },
    { name: 'Вторник', accent: '#6555f0', firstStart: '9:00', lessons: [] },
    { name: 'Среда', accent: '#6555f0', firstStart: '9:00', lessons: [] },
    { name: 'Четверг', accent: '#6555f0', firstStart: '9:00', lessons: [] },
    { name: 'Пятница', accent: '#6555f0', firstStart: '9:00', lessons: [] },
    { name: 'Суббота', accent: '#6555f0', firstStart: '9:00', lessons: [] },
  ],
};

function setBackgroundByWeekType(root, weekType) {
  root.style.backgroundImage = `url('${weekType === 'even' ? EVEN_BG : ODD_BG}')`;
}

function getWeekTitle(weekType) {
  return weekType === 'even' ? 'Чётная неделя' : 'Нечётная неделя';
}

function buildLessonRow(lesson, accentColor) {
  const row = document.createElement('div');
  row.setAttribute('data-name', 'Пара');
  row.className = 'flex flex-row items-center gap-[50px] text-left text-[48px] leading-none';

  const title = document.createElement('div');
  title.className = 'adjustLetterSpacing text-black font-normal';
  title.textContent = lesson.title;

  const room = document.createElement('div');
  room.className = 'not-italic';
  room.style.color = accentColor || '#6555f0';
  room.textContent = lesson.room;

  const time = document.createElement('div');
  time.className = 'not-italic text-[#323232] font-light';
  time.textContent = lesson.time;

  row.append(title, room, time);
  return row;
}

function buildDayCard(day, accent, weekType) {
  const card = document.createElement('article');
  card.setAttribute('data-name', 'День недели');
  card.className = 'bg-white rounded-[40px] px-[60px] py-[45px] flex flex-row gap-10 items-start min-h-[300px] h-[510px] w-[1282px]';

  const dayContent = document.createElement('div');
  dayContent.setAttribute('data-name', 'Контент дня');
  dayContent.className = 'flex flex-col gap-5 grow h-full justify-center';

  const dayTitle = document.createElement('div');
  dayTitle.className = 'text-left font-bold text-[64px] tracking-[0.96px]';
  dayTitle.style.color = accent;
  dayTitle.textContent = day.name;

  dayContent.appendChild(dayTitle);

  if (day.lessons && day.lessons.length > 0) {
    day.lessons.forEach((lesson) => {
      dayContent.appendChild(buildLessonRow(lesson, accent));
    });
  } else {
    const empty = document.createElement('div');
    empty.className = 'text-[#000000] text-[48px] tracking-[0.72px] adjustLetterSpacing leading-[40px]';
    empty.textContent = 'Отдыхаем на расслабоне, друзья';
    dayContent.appendChild(empty);
  }

  // Бейдж первой пары справа
  const firstBadge = document.createElement('div');
  firstBadge.setAttribute('data-name', 'Первая пара');
  firstBadge.className = 'w-40 flex flex-col items-center justify-center shrink-0';

  const timeBox = document.createElement('div');
  timeBox.setAttribute('data-name', 'Время начала');
  timeBox.className = 'rounded-[20px] text-white px-[33px] py-5 text-center flex items-center justify-center';
  timeBox.style.backgroundColor = weekType === 'even' ? '#8274fb' : '#ff656f';

  const t = document.createElement('div');
  t.className = 'text-[40px] font-bold leading-[40px]';
  t.textContent = day.firstStart || '';
  timeBox.appendChild(t);

  const label = document.createElement('div');
  label.className = 'text-center text-[20px] font-bold h-[40.095px] leading-[40px] text-[#8274fb]';
  label.style.color = weekType === 'even' ? '#8274fb' : '#ff656f';
  label.textContent = 'первая пара';

  firstBadge.append(timeBox, label);

  card.append(dayContent, firstBadge);
  return card;
}

function renderWeek(state) {
  const appRoot = document.getElementById('app');
  const grid = document.getElementById('daysGrid');
  const title = document.getElementById('weekTitle');
  grid.innerHTML = '';

  const weekType = state.weekType;
  // Используем локальные фоны, как просили
  appRoot.style.backgroundImage = `url('/assets/${weekType === 'even' ? 'purple_background.png' : 'orange_background.png'}')`;
  title.textContent = getWeekTitle(weekType);

  const days = weekType === 'even' ? (state.evenDays?.length ? state.evenDays : state.days) : state.days;
  days.forEach((day) => {
    const accent = weekType === 'even' ? '#6555f0' : (day.accent || '#ff2f00');
    grid.appendChild(buildDayCard(day, accent, weekType));
  });
}

function initControls(state) {}

// Инициализация
document.addEventListener('DOMContentLoaded', () => {
  // Можно заменить timetableData из внешнего JSON/fetch
  const state = JSON.parse(JSON.stringify(timetableData));
  initControls(state);
  renderWeek(state);
  // Масштабирование канвы под ширину экрана
  const canvas = document.getElementById('scale-canvas');
  const baseWidth = 2969; // ширина канвы (150px паддинги *2 + 2669 контент)
  function rescale() {
    const vw = window.innerWidth;
    const scale = Math.min(1, vw / baseWidth);
    canvas.style.transform = `scale(${scale})`;
  }
  window.addEventListener('resize', rescale);
  // Рескейл чуть позже, когда фон подставится
  setTimeout(rescale, 0);
});


