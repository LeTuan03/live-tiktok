using System;
using System.Diagnostics;
using System.IO;
using System.Threading;
using System.Windows.Forms;

namespace SoShop
{
    internal static class Program
    {
        /// <summary>Off with --no-browser (used by tests).</summary>
        public static bool OpenBrowser { get; private set; } = true;

        /// <summary>
        ///   SoShop.exe                 start SoShop and open the studio
        ///   SoShop.exe --data DIR      keep the data somewhere else (also SOSHOP_DATA_DIR)
        ///   SoShop.exe --no-browser    do not open the studio when ready
        ///   SoShop.exe --uninstall     remove SoShop (Installed apps runs this)
        /// </summary>
        [STAThread]
        private static int Main(string[] args)
        {
            string dataDir = Environment.GetEnvironmentVariable("SOSHOP_DATA_DIR");
            bool uninstall = false;
            for (int i = 0; i < args.Length; i++)
            {
                switch (args[i])
                {
                    case "--data" when i + 1 < args.Length:
                        dataDir = args[++i];
                        break;
                    case "--no-browser":
                        OpenBrowser = false;
                        break;
                    case "--uninstall":
                        uninstall = true;
                        break;
                }
            }

            Application.EnableVisualStyles();
            Application.SetCompatibleTextRenderingDefault(false);

            if (!File.Exists(Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "app", "python", "python.exe")))
            {
                MessageBox.Show(
                    "Thiếu các thành phần của SoShop (thư mục app). Hãy cài lại bằng SoShop-Setup.exe.",
                    "SoShop", MessageBoxButtons.OK, MessageBoxIcon.Error);
                return 1;
            }

            AppPaths.Init(dataDir);
            if (uninstall) return Uninstaller.Run();

            using (new Mutex(true, InstallInfo.MutexName, out bool first))
            {
                if (!first)
                {
                    // Already running: bring that window forward instead of starting twice.
                    try
                    {
                        using (var show = EventWaitHandle.OpenExisting(InstallInfo.ShowEventName)) show.Set();
                    }
                    catch (WaitHandleCannotBeOpenedException)
                    {
                    }
                    return 0;
                }
                Application.ThreadException += (s, e) => Crash(e.Exception);
                AppDomain.CurrentDomain.UnhandledException += (s, e) => Crash(e.ExceptionObject as Exception);
                Log.Write($"SoShop {AppPaths.Version} starting, data in {AppPaths.DataDir}");
                Application.Run(new MainForm());
                Log.Write("SoShop closed");
            }
            return 0;
        }

        private static void Crash(Exception error)
        {
            Log.Write("unexpected error: " + error);
            MessageBox.Show("SoShop gặp lỗi không mong muốn:\n\n" + error?.Message +
                            "\n\nChi tiết nằm trong logs\\launcher.log ở thư mục dữ liệu.",
                "SoShop", MessageBoxButtons.OK, MessageBoxIcon.Error);
        }
    }

    /// <summary>SoShop.exe --uninstall, registered with Windows' Installed apps by the setup.</summary>
    internal static class Uninstaller
    {
        public static int Run()
        {
            if (InstallInfo.IsLauncherRunning())
            {
                MessageBox.Show("SoShop đang chạy. Hãy đóng cửa sổ SoShop rồi gỡ cài đặt lại.", "Gỡ SoShop",
                    MessageBoxButtons.OK, MessageBoxIcon.Information);
                return 1;
            }
            if (MessageBox.Show("Gỡ SoShop khỏi máy này?", "Gỡ SoShop", MessageBoxButtons.YesNo,
                    MessageBoxIcon.Question) != DialogResult.Yes)
                return 0;
            bool removeData = MessageBox.Show(
                "Xóa luôn dữ liệu (sản phẩm, video, phiên live, cài đặt)?\n\n" +
                "Chọn \"No\" để giữ lại: cài lại SoShop sau này vẫn dùng tiếp được.\n\n" + AppPaths.DataDir,
                "Gỡ SoShop", MessageBoxButtons.YesNo, MessageBoxIcon.Warning, MessageBoxDefaultButton.Button2) == DialogResult.Yes;

            // A crash may have left the database server running; it locks the data folder.
            var pgCtl = Path.Combine(AppPaths.PgBin, "pg_ctl.exe");
            if (File.Exists(Path.Combine(AppPaths.PgData, "PG_VERSION")))
                Proc.Run(pgCtl, Proc.Args("stop", "-D", AppPaths.PgData, "-m", "fast", "-w"), AppPaths.PgBin, null, null, 60_000);

            foreach (var link in new[] { InstallInfo.StartMenuShortcut, InstallInfo.DesktopShortcut })
                if (File.Exists(link)) File.Delete(link);
            InstallInfo.RemoveUninstallEntry();

            if (removeData)
            {
                try
                {
                    Directory.Delete(AppPaths.DataDir, true);
                }
                catch (Exception e) when (e is IOException || e is UnauthorizedAccessException)
                {
                    MessageBox.Show("Không xóa hết được thư mục dữ liệu:\n" + AppPaths.DataDir + "\n\nHãy xóa bằng tay.",
                        "Gỡ SoShop", MessageBoxButtons.OK, MessageBoxIcon.Warning);
                }
            }

            // This exe lives in the folder being removed: delete it after we exit. Only
            // what the setup put there is removed, then the folder if nothing else is left.
            var dir = AppPaths.ProgramDir;
            var script = $"ping -n 3 127.0.0.1 >nul & rmdir /s /q \"{dir}\\app\" & del /f /q \"{dir}\\{InstallInfo.LauncherExe}\" & rmdir \"{dir}\"";
            Process.Start(new ProcessStartInfo("cmd.exe", "/d /c " + script)
            {
                UseShellExecute = false,
                CreateNoWindow = true,
                WorkingDirectory = Path.GetTempPath(),
            })?.Dispose();

            MessageBox.Show("Đã gỡ SoShop.", "Gỡ SoShop", MessageBoxButtons.OK, MessageBoxIcon.Information);
            return 0;
        }
    }
}
