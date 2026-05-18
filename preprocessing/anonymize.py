import cv2
import os
from pdf2image import convert_from_path
from PIL import Image
import numpy as np

drawing = False
ix, iy = -1, -1
rectangles = []
img = None
img_copy = None


def draw_rectangle(event, x, y, flags, param):
    global drawing, ix, iy, img_copy, rectangles

    if event == cv2.EVENT_LBUTTONDOWN:
        drawing = True
        ix, iy = x, y

    elif event == cv2.EVENT_MOUSEMOVE and drawing:
        img_copy = img.copy()
        for (x1, y1, x2, y2) in rectangles:
            cv2.rectangle(img_copy, (x1, y1), (x2, y2), (0, 0, 0), -1)
        cv2.rectangle(img_copy, (ix, iy), (x, y), (0, 0, 0), -1)

    elif event == cv2.EVENT_LBUTTONUP:
        drawing = False
        rectangles.append((min(ix, x), min(iy, y), max(ix, x), max(iy, y)))
        img_copy = img.copy()
        for (x1, y1, x2, y2) in rectangles:
            cv2.rectangle(img_copy, (x1, y1), (x2, y2), (0, 0, 0), -1)


def anonymise_image(input_path, output_path):
    global img, img_copy, rectangles

    img = cv2.imread(input_path)
    if img is None:
        print(f"Cannot read {input_path}")
        return False

    img_copy = img.copy()
    rectangles = []

    window = "Anonymise — drag to redact, S=save, R=reset, Q=quit"
    cv2.namedWindow(window, cv2.WINDOW_NORMAL)
    cv2.resizeWindow(window, 900, 1200)
    cv2.setMouseCallback(window, draw_rectangle)

    print(f"\nFile: {input_path}")
    print("Drag to draw black boxes. S=save | R=reset | Q=quit")

    # macOS fix: show first frame and wait
    cv2.imshow(window, img_copy)
    cv2.waitKey(1)

    while True:
        cv2.imshow(window, img_copy)
        key = cv2.waitKey(10) & 0xFF

        if key == ord('s'):
            final = img.copy()
            for (x1, y1, x2, y2) in rectangles:
                cv2.rectangle(final, (x1, y1), (x2, y2), (0, 0, 0), -1)
            cv2.imwrite(output_path, final)
            print(f"Saved: {output_path}")
            cv2.destroyWindow(window)
            return True

        elif key == ord('r'):
            rectangles = []
            img_copy = img.copy()
            print("Reset")

        elif key == ord('q'):
            print("Skipped")
            cv2.destroyWindow(window)
            return False


def images_to_pdf(image_paths, output_pdf_path):
    images = [Image.open(p).convert("RGB") for p in image_paths]
    first, rest = images[0], images[1:]
    first.save(output_pdf_path, save_all=True, append_images=rest)



def anonymise_pdf(pdf_path, output_folder):
    try:
        pages = convert_from_path(pdf_path, dpi=200)
    except Exception:
        print(f"Cannot read {pdf_path}")
        return

    base = os.path.splitext(os.path.basename(pdf_path))[0]
    page_images = []

    for i, page in enumerate(pages):
        cv_img = cv2.cvtColor(np.array(page), cv2.COLOR_RGB2BGR)
        page_path = os.path.join(output_folder, f"{base}_page_{i}.png")

        cv2.imwrite(page_path, cv_img)

        edited = anonymise_image(page_path, page_path)

        if edited:
            page_images.append(page_path)

    if page_images:
        output_pdf = os.path.join(output_folder, f"{base}_ANONYMIZED.pdf")
        images_to_pdf(page_images, output_pdf)
        print(f"Saved anonymized PDF: {output_pdf}")

        for p in page_images:
            os.remove(p)


input_folder = "documents/raw"
output_folder = "documents/anonymized_docs"
os.makedirs(output_folder, exist_ok=True)

files = sorted(os.listdir(input_folder))
print(f"Found {len(files)} files to anonymise")

for fname in files:
    input_path = os.path.join(input_folder, fname)

    if fname.lower().endswith(".pdf"):
        anonymise_pdf(input_path, output_folder)
    elif fname.lower().endswith((".png", ".jpg", ".jpeg")):
        output_path = os.path.join(output_folder, fname)
        anonymise_image(input_path, output_path)
    else:
        print(f"Skipping unsupported file: {fname}")

print("\nDone. Anonymised files saved in anonymized_docs/")
