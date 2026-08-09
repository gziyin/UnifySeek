import { useEffect, useState } from "react";
import bg1 from "../assets/bg-1.png";
import bg2 from "../assets/bg-2.png";

const BG_KEY = "unifyseek.bg";
const BG_URLS: Record<number, string> = { 1: bg1, 2: bg2 };

function readChoice(): number {
  const raw = localStorage.getItem(BG_KEY);
  const n = raw === "1" ? 1 : raw === "2" ? 2 : 2; // 默认背景 2（薄雾远山）
  return n;
}

type Props = {
  choice: number;
};

/**
 * 双背景层 + 渐变遮罩。
 * 选中的背景立即渲染；另一张在首帧后（懒加载）再挂载。
 * opacity 交叉淡入淡出由 CSS transition 处理。
 */
export function Background({ choice }: Props) {
  const [lazyLoaded, setLazyLoaded] = useState(false);

  useEffect(() => {
    setLazyLoaded(false);
    const id = window.requestAnimationFrame(() => {
      window.setTimeout(() => setLazyLoaded(true), 120);
    });
    return () => window.cancelAnimationFrame(id);
  }, [choice]);

  const bg1Loaded = choice === 1 || lazyLoaded;
  const bg2Loaded = choice === 2 || lazyLoaded;

  return (
    <>
      <div
        className={`bg-layer ${choice === 1 ? "active" : ""}`}
        style={{ backgroundImage: bg1Loaded ? `url(${BG_URLS[1]})` : undefined }}
        aria-hidden="true"
      />
      <div
        className={`bg-layer ${choice === 2 ? "active" : ""}`}
        style={{ backgroundImage: bg2Loaded ? `url(${BG_URLS[2]})` : undefined }}
        aria-hidden="true"
      />
      <div className="bg-overlay" aria-hidden="true" />
    </>
  );
}

export function readBgChoice(): 1 | 2 {
  return readChoice() as 1 | 2;
}
