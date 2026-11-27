using System;
using System.Collections.Generic;
using System.IO;
using System.IO.Compression;
using System.Linq;
using System.Text;
using System.Text.RegularExpressions;

namespace SoShop
{
    /// <summary>
    /// "Sao lưu dữ liệu" and "Khôi phục dữ liệu": one zip holding the database
    /// and the media files, to carry SoShop's data to another machine.
    /// tools/export-data.ps1 writes the same format from the developer setup
    /// and from the Docker bundle.
    ///
    ///   manifest.json  {"format": 1, ...}; "baseline": "0006" when the source
    ///                  database predates schema_migrations (see migrate.py)
    ///   db.dump        pg_dump -Fc of the soshop database
    ///   data/...       DATA_DIR (uploads, Flow videos, avatars) minus model
    ///                  weights and scratch space, which are re-created
    /// </summary>
    internal static class DataBackup
    {
        private static readonly string[] Skipped = { "models", "work", "cache", "tmp" };
        private static readonly HashSet<string> AlreadyCompressed = new HashSet<string>(StringComparer.OrdinalIgnoreCase)
        {
            ".mp4", ".mov", ".webm", ".mkv", ".m4a", ".mp3", ".aac", ".jpg", ".jpeg", ".png", ".webp", ".gif", ".zip",
        };

        public static void Backup(Stack stack, string zipPath, Action<string> say)
        {
            var dump = Path.Combine(AppPaths.TempDir, "db.dump");
            say("Đang xuất cơ sở dữ liệu...");
            stack.DumpDatabase(dump);

            var files = MediaFiles().ToList();
            var partial = zipPath + ".partial";
            if (File.Exists(partial)) File.Delete(partial);
            using (var zip = ZipFile.Open(partial, ZipArchiveMode.Create))
            {
                AddText(zip, "manifest.json",
                    "{\"format\": 1, \"app\": \"SoShop\", \"source\": \"desktop\", \"version\": \"" + AppPaths.Version +
                    "\", \"created\": \"" + DateTime.Now.ToString("o") + "\"}");
                zip.CreateEntryFromFile(dump, "db.dump", CompressionLevel.Optimal);
                for (int i = 0; i < files.Count; i++)
                {
                    if (i % 10 == 0) say($"Đang nén video và hình ảnh ({i + 1}/{files.Count})...");
                    var name = "data/" + files[i].Substring(AppPaths.MediaDir.Length + 1).Replace('\\', '/');
                    var level = AlreadyCompressed.Contains(Path.GetExtension(files[i]))
                        ? CompressionLevel.NoCompression
                        : CompressionLevel.Optimal;
                    zip.CreateEntryFromFile(files[i], name, level);
                }
            }
            if (File.Exists(zipPath)) File.Delete(zipPath);
            File.Move(partial, zipPath);
            File.Delete(dump);
        }

        public static void Restore(Stack stack, string zipPath, Action<string> say)
        {
            using (var zip = ZipFile.OpenRead(zipPath))
            {
                var dumpEntry = zip.GetEntry("db.dump");
                if (dumpEntry == null)
                    throw new UserError("File này không phải bản sao lưu SoShop (không có db.dump).");
                var baseline = Regex.Match(ReadText(zip.GetEntry("manifest.json")), "\"baseline\"\\s*:\\s*\"(\\d+)\"");
                var dump = Path.Combine(AppPaths.TempDir, "restore.dump");
                say("Đang đọc bản sao lưu...");
                dumpEntry.ExtractToFile(dump, true);

                say("Đang tạm dừng SoShop...");
                stack.PauseApp();
                try
                {
                    say("Đang nạp cơ sở dữ liệu...");
                    stack.RestoreDatabase(dump);
                    ClearMedia();
                    var root = Path.GetFullPath(AppPaths.MediaDir).TrimEnd('\\') + "\\";
                    var entries = zip.Entries
                        .Where(e => e.FullName.StartsWith("data/", StringComparison.Ordinal) && !e.FullName.EndsWith("/"))
                        .ToList();
                    for (int i = 0; i < entries.Count; i++)
                    {
                        if (i % 10 == 0) say($"Đang chép video và hình ảnh ({i + 1}/{entries.Count})...");
                        var relative = entries[i].FullName.Substring("data/".Length).Replace('/', '\\');
                        var target = Path.GetFullPath(Path.Combine(root, relative));
                        if (!target.StartsWith(root, StringComparison.OrdinalIgnoreCase)) continue; // "../" in a crafted zip
                        Directory.CreateDirectory(Path.GetDirectoryName(target));
                        entries[i].ExtractToFile(target, true);
                    }
                    say("Đang cập nhật cấu trúc dữ liệu...");
                    stack.Migrate(baseline.Success ? baseline.Groups[1].Value : null);
                }
                catch
                {
                    // Leave a working (if empty) database behind rather than a half-restored one.
                    try { stack.Migrate(); } catch (UserError) { }
                    throw;
                }
                finally
                {
                    try { File.Delete(dump); } catch (IOException) { }
                    stack.ResumeApp();
                }
            }
        }

        private static IEnumerable<string> MediaFiles()
        {
            var root = AppPaths.MediaDir;
            foreach (var file in Directory.GetFiles(root)) yield return file;
            foreach (var dir in Directory.GetDirectories(root))
            {
                if (Skipped.Contains(Path.GetFileName(dir), StringComparer.OrdinalIgnoreCase)) continue;
                foreach (var file in Directory.EnumerateFiles(dir, "*", SearchOption.AllDirectories)) yield return file;
            }
        }

        private static void ClearMedia()
        {
            var root = AppPaths.MediaDir;
            foreach (var file in Directory.GetFiles(root)) File.Delete(file);
            foreach (var dir in Directory.GetDirectories(root))
            {
                if (Skipped.Contains(Path.GetFileName(dir), StringComparer.OrdinalIgnoreCase)) continue;
                Directory.Delete(dir, true);
            }
        }

        private static void AddText(ZipArchive zip, string name, string text)
        {
            var entry = zip.CreateEntry(name);
            using (var writer = new StreamWriter(entry.Open(), new UTF8Encoding(false))) writer.Write(text);
        }

        private static string ReadText(ZipArchiveEntry entry)
        {
            if (entry == null) return "";
            using (var reader = new StreamReader(entry.Open(), Encoding.UTF8)) return reader.ReadToEnd();
        }
    }
}
