import { layoutWithLines, prepareWithSegments } from "@chenglou/pretext";

export type ClampedTextLayout = {
  displayText: string;
  isClamped: boolean;
};

const TRAILING_PUNCTUATION_PATTERN = /[.。!！?？,，;；:：\s]+$/u;

export function clampTextByLines(
  text: string,
  font: string,
  width: number,
  lineHeight: number,
  maxLines: number,
): ClampedTextLayout {
  const content = text.trim();

  if (!content || width <= 0 || maxLines <= 0) {
    return {
      displayText: content,
      isClamped: false,
    };
  }

  const prepared = prepareWithSegments(content, font);
  const { lines } = layoutWithLines(prepared, width, lineHeight);

  if (lines.length <= maxLines) {
    return {
      displayText: content,
      isClamped: false,
    };
  }

  const visibleLines = lines.slice(0, maxLines).map((line) => line.text.replace(/\s+$/u, ""));
  const lastLineIndex = visibleLines.length - 1;
  const trimmedLastLine = visibleLines[lastLineIndex].replace(TRAILING_PUNCTUATION_PATTERN, "");

  visibleLines[lastLineIndex] = `${trimmedLastLine || visibleLines[lastLineIndex]}…`;

  return {
    displayText: visibleLines.join("\n"),
    isClamped: true,
  };
}
