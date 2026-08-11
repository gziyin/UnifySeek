import { useEffect } from "react";
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
 * 挂载时即预加载两张背景，两个 .bg-layer 始终持有 backgroundImage，
 * 切换仅靠 .active 的 opacity 交叉淡入，避免旧实现懒加载导致的闪烁/白屏。
 */
export function Background({ choice }: Props) {
  useEffect(() => {
    // 预加载两张背景，保证切换时交叉淡入丝滑（无首次加载空白）。
    for (const url of Object.values(BG_URLS)) {
      const img = new Image();
      img.src = url;
    }
  }, []);

  return (
    <>
      <div
        className={`bg-layer ${choice === 1 ? "active" : ""}`}
        style={{ backgroundImage: `url(${BG_URLS[1]})` }}
        aria-hidden="true"
      />
      <div
        className={`bg-layer ${choice === 2 ? "active" : ""}`}
        style={{ backgroundImage: `url(${BG_URLS[2]})` }}
        aria-hidden="true"
      />
      <div className="bg-overlay" aria-hidden="true" />
    </>
  );
}

export function readBgChoice(): 1 | 2 {
  return readChoice() as 1 | 2;
}
