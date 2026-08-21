import { describe, expect, it, vi } from "vitest";
import { isFileDrag, uploadFilesSequentially } from "../src/components/UploadSection";

describe("UploadSection drag and drop", () => {
  it("recognizes file drags without treating text drags as uploads", () => {
    expect(isFileDrag(["Files"])).toBe(true);
    expect(isFileDrag(["text/plain"])).toBe(false);
  });

  it("uploads every dropped file sequentially", async () => {
    const first = { name: "first.pdf" } as File;
    const second = { name: "second.docx" } as File;
    const onUpload = vi.fn(async () => undefined);

    await uploadFilesSequentially([first, second], onUpload);

    expect(onUpload.mock.calls).toEqual([[first], [second]]);
  });
});
