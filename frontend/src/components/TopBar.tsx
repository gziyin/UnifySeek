import type { RefObject } from "react";

type Props = {
  bgChoice: 1 | 2;
  onBgChange: (choice: 1 | 2) => void;
  drawerOpen: boolean;
  onOpenDrawer: () => void;
  historyRef?: RefObject<HTMLButtonElement | null>;
};

const GITHUB_URL = "https://github.com/gziyin/UnifySeek";

export function TopBar({
  bgChoice,
  onBgChange,
  drawerOpen,
  onOpenDrawer,
  historyRef,
}: Props) {
  return (
    <header className="top-bar">
      <div className="top-bar-left">
        <button
          ref={historyRef}
          type="button"
          className="history-btn"
          onClick={onOpenDrawer}
          aria-expanded={drawerOpen}
          aria-haspopup="dialog"
        >
          <span aria-hidden="true">☰</span>
          <span>历史记录</span>
        </button>
      </div>
      <div className="top-bar-right">
        <div className="bg-toggle" role="group" aria-label="背景图切换">
          <button
            type="button"
            className={bgChoice === 1 ? "active" : ""}
            onClick={() => onBgChange(1)}
            aria-pressed={bgChoice === 1}
          >
            背景 1
          </button>
          <button
            type="button"
            className={bgChoice === 2 ? "active" : ""}
            onClick={() => onBgChange(2)}
            aria-pressed={bgChoice === 2}
          >
            背景 2
          </button>
        </div>
        <a href={GITHUB_URL} target="_blank" rel="noreferrer" className="github-btn">
          <span aria-hidden="true">★</span>
          <span>GitHub</span>
        </a>
      </div>
    </header>
  );
}
