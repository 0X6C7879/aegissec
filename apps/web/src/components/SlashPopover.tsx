import type { SlashCatalogItem } from "../types/slash";

type SlashPopoverProps = {
  id: string;
  items: SlashCatalogItem[];
  activeIndex: number;
  onHoverItem: (index: number) => void;
  onSelectItem: (item: SlashCatalogItem) => void;
};

function getBadgeLabel(item: SlashCatalogItem): string | null {
  if (item.badge && item.badge.trim().length > 0) {
    return item.badge.trim();
  }

  if (item.type === "skill" || item.source === "skill") {
    return "skill";
  }

  if (item.type === "mcp" || item.source === "mcp") {
    return "mcp";
  }

  return null;
}

export function SlashPopover({
  id,
  items,
  activeIndex,
  onHoverItem,
  onSelectItem,
}: SlashPopoverProps) {
  return (
    <div id={id} className="slash-popover" role="listbox" aria-label="斜杠指令">
      {items.map((item, index) => {
        const isActive = index === activeIndex;
        const isDisabled = item.disabled === true;
        const badgeLabel = getBadgeLabel(item);

        return (
          <button
            key={item.id}
            type="button"
            role="option"
            aria-selected={isActive}
            aria-disabled={isDisabled}
            disabled={isDisabled}
            className={`slash-popover-item${isActive ? " slash-popover-item-active" : ""}${isDisabled ? " slash-popover-item-disabled" : ""}`}
            onMouseEnter={() => {
              if (!isDisabled) {
                onHoverItem(index);
              }
            }}
            onMouseDown={(event) => event.preventDefault()}
            onClick={() => {
              if (!isDisabled) {
                onSelectItem(item);
              }
            }}
          >
            <div className="slash-popover-item-header">
              <span className="slash-popover-item-trigger">/{item.trigger}</span>
              <div className="slash-popover-item-meta">
                {badgeLabel ? (
                  <span className="management-status-badge slash-popover-item-badge">
                    {badgeLabel}
                  </span>
                ) : null}
                {item.keybind ? (
                  <span className="slash-popover-item-keybind">{item.keybind}</span>
                ) : null}
              </div>
            </div>
            {item.description ? (
              <p className="slash-popover-item-description">{item.description}</p>
            ) : null}
          </button>
        );
      })}
    </div>
  );
}
