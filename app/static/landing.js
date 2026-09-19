/* Пульс Орбиты — лендинг.
   Рисованные звёзды: контурные, чёрно-бело-розовые.
   Часть медленно летает по блоку, часть мерцает в углах. */

const NS = "http://www.w3.org/2000/svg";

/* Точки звезды с чередованием внешнего и внутреннего радиуса. */
function starPoints(spikes, outer, inner) {
  const points = [];
  for (let i = 0; i < spikes * 2; i += 1) {
    const radius = i % 2 === 0 ? outer : inner;
    const angle = (Math.PI * i) / spikes - Math.PI / 2;
    points.push(`${(50 + radius * Math.cos(angle)).toFixed(1)},${(50 + radius * Math.sin(angle)).toFixed(1)}`);
  }
  return points.join(" ");
}

/* Четырёхлучевая искра с вогнутыми гранями — основной мотив. */
const SPARKLE = "M50 3 C53.5 38 62 46.5 97 50 C62 53.5 53.5 62 50 97 C46.5 62 38 53.5 3 50 C38 46.5 46.5 38 50 3 Z";

const SHAPES = {
  sparkle: () => `<path d="${SPARKLE}" />`,
  burst: () => `<polygon points="${starPoints(8, 47, 15)}" />`,
  star5: () => `<polygon points="${starPoints(5, 47, 19)}" />`,
  star6: () => `<polygon points="${starPoints(6, 46, 22)}" />`,
  /* Двойной контур: звезда в звезде, как на рисунке от руки. */
  double: () => `<polygon points="${starPoints(5, 47, 19)}" /><polygon points="${starPoints(5, 27, 11)}" />`,
  /* Лучики-асterisk: три пересекающиеся линии. */
  spark: () =>
    [0, 60, 120]
      .map(deg => `<line x1="50" y1="6" x2="50" y2="94" transform="rotate(${deg} 50 50)" />`)
      .join(""),
};

function makeStar(config) {
  const figure = document.createElement("span");
  figure.className = `lp-star lp-star--${config.motion}`;
  figure.style.cssText = [
    config.top !== undefined ? `top:${config.top}` : `bottom:${config.bottom}`,
    config.left !== undefined ? `left:${config.left}` : `right:${config.right}`,
    `width:${config.size}px`,
    `height:${config.size}px`,
    `color:${config.color}`,
    `--dx:${config.dx || "0px"}`,
    `--dy:${config.dy || "0px"}`,
    `--rot:${config.rot || "0deg"}`,
    `--dim:${config.dim === undefined ? 0.35 : config.dim}`,
    `animation-duration:${config.duration}s`,
    `animation-delay:-${config.delay}s`,
    `stroke-width:${config.weight || 5}`,
  ].join(";");
  figure.innerHTML =
    `<svg viewBox="0 0 100 100" aria-hidden="true" focusable="false">${SHAPES[config.shape]()}</svg>`;
  return figure;
}

const WHITE = "#FFFFFF";
const PINK = "#F2789F";
const GREY = "#9A9AAE";

/* Летающие — по краям сцены, подальше от заголовка. */
const DRIFTING = [
  { shape: "sparkle", motion: "drift", size: 52, top: "12%", left: "24%", color: WHITE, dx: "34px", dy: "26px", rot: "25deg", duration: 19, delay: 0 },
  { shape: "star5", motion: "drift", size: 34, top: "26%", left: "8%", color: PINK, dx: "-26px", dy: "32px", rot: "-30deg", duration: 23, delay: 5 },
  { shape: "spark", motion: "drift", size: 26, top: "44%", left: "15%", color: GREY, dx: "22px", dy: "-28px", rot: "40deg", duration: 17, delay: 9 },
  { shape: "burst", motion: "drift", size: 40, top: "62%", left: "30%", color: WHITE, dx: "-30px", dy: "-22px", rot: "35deg", duration: 25, delay: 3 },
  { shape: "double", motion: "drift", size: 46, top: "18%", right: "26%", color: PINK, dx: "-28px", dy: "30px", rot: "28deg", duration: 21, delay: 12 },
  { shape: "sparkle", motion: "drift", size: 30, top: "52%", right: "12%", color: WHITE, dx: "26px", dy: "-24px", rot: "-22deg", duration: 18, delay: 7 },
  { shape: "star6", motion: "drift", size: 36, bottom: "20%", left: "44%", color: GREY, dx: "30px", dy: "-30px", rot: "45deg", duration: 26, delay: 15 },
  { shape: "spark", motion: "drift", size: 22, bottom: "34%", right: "30%", color: PINK, dx: "-24px", dy: "-26px", rot: "-35deg", duration: 20, delay: 2 },
];

/* Мерцающие — кучками в нижнем левом и верхнем правом углах. */
const TWINKLING = [
  /* нижний левый */
  { shape: "sparkle", motion: "twinkle", size: 58, bottom: "12%", left: "6%", color: WHITE, duration: 3.4, delay: 0 },
  { shape: "star5", motion: "twinkle", size: 30, bottom: "24%", left: "3%", color: PINK, duration: 4.1, delay: 1.5 },
  { shape: "spark", motion: "twinkle", size: 20, bottom: "6%", left: "17%", color: WHITE, duration: 2.8, delay: 0.7 },
  { shape: "double", motion: "twinkle", size: 38, bottom: "5%", left: "27%", color: GREY, duration: 5.2, delay: 2.4 },
  { shape: "burst", motion: "twinkle", size: 24, bottom: "20%", left: "20%", color: PINK, duration: 3.6, delay: 3.1 },
  /* верхний правый */
  { shape: "burst", motion: "twinkle", size: 54, top: "10%", right: "7%", color: WHITE, duration: 3.9, delay: 0.4 },
  { shape: "sparkle", motion: "twinkle", size: 28, top: "5%", right: "19%", color: PINK, duration: 3.1, delay: 2 },
  { shape: "spark", motion: "twinkle", size: 22, top: "24%", right: "4%", color: WHITE, duration: 2.6, delay: 1.1 },
  { shape: "star5", motion: "twinkle", size: 34, top: "22%", right: "17%", color: GREY, duration: 4.6, delay: 3.4 },
  { shape: "double", motion: "twinkle", size: 26, top: "3%", right: "31%", color: PINK, duration: 5, delay: 1.8 },
];

const sky = document.querySelector(".lp-sky");
if (sky) {
  const layer = document.createElement("div");
  layer.className = "lp-star-layer";
  layer.setAttribute("aria-hidden", "true");
  for (const config of [...DRIFTING, ...TWINKLING]) layer.appendChild(makeStar(config));
  sky.prepend(layer);
}
