# Where Did I Save That?

A small, local-first Windows desktop app for finding files in folders you choose. It searches file names and extracted text, then opens the original file. The index stays in your Windows user profile; this app does not upload your files.

## Current features

- Choose one or more folders to search.
- Search filenames and text in PDF, DOCX, XLSX, PPTX, TXT, Markdown, CSV, JSON, HTML, and common code/text formats.
- Search indexed pictures with a natural-language description, such as “a person in a blue shirt standing by the sea.” Results are ranked by visual similarity.
- Refresh the index; unchanged files are skipped.
- Stop an indexing run.
- Open a result in its normal Windows app.
- Keep settings and the SQLite search index locally under `%LOCALAPPDATA%\WhereDidISaveThat`.
- Run picture matching locally with a one-time model download (about 150 MB); picture files are not uploaded.
- Remove a selected folder and its indexed results.

## Requirements

- Windows 10 or Windows 11, 64-bit.
- For running from source: Python 3.10 or newer and Git.

## Run from source

1. Install Python and Git if they are not already installed.
2. Open PowerShell in this project folder.
3. Create and activate a virtual environment:

   ```powershell
   python -m venv .venv
   .\.venv\Scripts\Activate.ps1
   ```

4. Install the app's packages and start it:

   ```powershell
   python -m pip install -r requirements.txt
   python app.py
   ```

If PowerShell blocks activation, run the app without activating the environment:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe app.py
```

## Find pictures by describing them

1. Add a folder that contains pictures and choose **Index folders**. On the first picture, the app downloads the visual model (about 150 MB) from Hugging Face. An internet connection is needed for this first download.
2. Select **Search pictures by description**.
3. Describe the scene in the search box, such as `a person wearing a blue shirt and black pants standing in the sea`, and press Enter.
4. The app ranks the closest matches. Select a result and choose **Open result** to view the original picture.

Picture indexing and matching run on this PC. The model download is the only network step for this feature. Common image types, including HEIC/HEIF phone photos, are supported.

On first launch, the app creates `Pictures\Photos` and adds a desktop shortcut named **Photos - Where Did I Save That**. Save or copy pictures there, then choose **Index folders** in the app before searching.

## Install on Windows

For the easiest install, open the GitHub **Releases** page and download `WhereDidISaveThat-Setup.exe`. Double-click it, follow the short setup wizard, and choose **Finish**. Setup installs the app for your Windows account, adds a Start menu shortcut, creates `Pictures\Photos`, and adds a shortcut to that folder on your desktop. Python and Git are not required.

The standalone `WhereDidISaveThat.exe` is also available for people who prefer a portable app. The community build is not code-signed, so Windows may show a publisher warning.

Picture search downloads its visual model the first time you index pictures. This one-time download is about 154 MB; later picture indexing and matching run locally on this PC.

To make a release yourself, push a version tag such as `v0.1.0`. The GitHub Actions workflow builds the Windows installer and standalone app, then attaches both to a GitHub Release. See [Publishing a release](#publishing-a-release).

## Publishing a release

1. Create a new **public** repository on GitHub named `where-did-i-save-that` (do not add a README or license there, because they already exist here).
2. In PowerShell, from this project folder, set your Git identity if you have not done so before:

   ```powershell
   git config --global user.name "Your Name"
   git config --global user.email "you@example.com"
   ```

3. Save and publish the project:

   ```powershell
   git init
   git add .
   git commit -m "Build first Windows version"
   git branch -M main
   git remote add origin https://github.com/YOUR-USERNAME/where-did-i-save-that.git
   git push -u origin main
   ```

4. Create and push a release tag:

   ```powershell
   git tag v0.1.0
   git push origin v0.1.0
   ```

5. On GitHub, open **Actions** and wait for **Build Windows release** to finish. The workflow publishes `WhereDidISaveThat-Setup.exe` and `WhereDidISaveThat.exe` to the repository's Releases page.

Replace `YOUR-USERNAME` with your GitHub username. Choose your GitHub username/email for the Git settings; do not paste passwords or access tokens into this project.

## Privacy and limitations

- Folder selection is opt-in. The app scans only selected folders.
- The database and settings are stored in your local Windows profile. No account is required.
- Picture search downloads a quantized CLIP model from [Hugging Face](https://huggingface.co/Xenova/clip-vit-base-patch32) on first use. After that download, image indexing and description matching run locally. Your image files and search text are not sent to the model host.
- Picture matching ranks images by similarity; it can miss details or return imperfect matches. It is not face recognition and cannot reliably confirm who a person is.
- Image-only search covers common formats: JPG, PNG, BMP, WEBP, GIF, and TIFF.
- Search is based on names and extracted text. Scanned image-only PDFs, pictures, and handwriting are not read (no OCR yet).
- Password-protected, damaged, or unsupported documents may have only their filenames indexed.
- Files larger than 50 MB are skipped. The indexer skips common generated folders such as `.git`, `node_modules`, and virtual environments.
- Removing a folder in the app removes its indexed results and stops future scans; it does not delete original files.
- This is an early release. Review the source and start by indexing folders you are comfortable searching.

## Contributing

Bug reports and practical suggestions are welcome through GitHub Issues. Please do not include private documents, file contents, or personal data in issue reports.

## License

MIT. See [LICENSE](LICENSE).

## Image model attribution

Picture search uses the quantized ONNX model from [Xenova/clip-vit-base-patch32](https://huggingface.co/Xenova/clip-vit-base-patch32), derived from [OpenAI CLIP](https://huggingface.co/openai/clip-vit-base-patch32). Model files are downloaded on first use and remain in the local application data folder.
