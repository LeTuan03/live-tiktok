using System;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.IO.Compression;
using System.Linq;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using System.Windows.Forms;

namespace SoShop
{
    internal static class SetupProgram
    {
        /// <summary>
        ///   SoShop-Setup.exe                      the install window
        ///   SoShop-Setup.exe --silent [--run]     install without questions (and start SoShop)
        ///   --dir DIR          install somewhere else
        ///   --no-shortcuts     no Start menu or Desktop shortcut
        ///   --no-registry      no entry in Windows' Installed apps
        ///   --payload FILE     read the app from a separate zip (development)
        /// </summary>
        [STAThread]
        private static int Main(string[] args)
        {
            string dir = null, payloadPath = null;
            bool silent = false, shortcuts = true, registry = true, run = false;
            for (int i = 0; i < args.Length; i++)
            {
                switch (args[i])
                {
                    case "--dir" when i + 1 < args.Length: dir = args[++i]; break;
                    case "--payload" when i + 1 < args.Length: payloadPath = args[++i]; break;
                    case "--silent": silent = true; break;
                    case "--run": run = true; break;
                    case "--no-shortcuts": shortcuts = false; break;
                    case "--no-registry": registry = false; break;
                }
            }

            Application.EnableVisualStyles();
            Application.SetCompatibleTextRenderingDefault(false);

            Payload payload;
            try
            {
                payload = Payload.Open(payloadPath ?? Application.ExecutablePath);
            }
            catch (Exception e) when (e is IOException || e is InvalidDataException || e is UnauthorizedAccessException)
            {
                if (!silent)
                {
                    MessageBox.Show("Bộ cài bị hỏng hoặc chưa tải về đầy đủ. Hãy tải lại SoShop-Setup.exe.\n\n" + e.Message,
                        "Cài đặt SoShop", MessageBoxButtons.OK, MessageBoxIcon.Error);
                }
                return 2;
            }

            using (payload)
            {
                dir = dir ?? InstallInfo.InstalledDir() ?? InstallInfo.DefaultInstallDir();
                if (!silent)
                {
                    Application.Run(new SetupForm(payload, dir, shortcuts, registry));
                    return 0;
                }
                try
                {
                    var installer = new Installer(payload, dir) { StartMenu = shortcuts, Desktop = shortcuts, Register = registry };
                    installer.Install((percent, message) => { });
                    if (run) Process.Start(Path.Combine(dir, InstallInfo.LauncherExe))?.Dispose();
                    return 0;
                }
                catch (SetupError e)
                {
                    Console.Error.WriteLine(e.Message);
                    return 1;
                }
            }
        }
    }

    internal sealed class SetupError : Exception
    {
        public SetupError(string message) : base(message) { }
    }

    /// <summary>
    /// The app, appended to this exe as a zip by build-app.ps1:
    /// [setup exe][zip][zip offset: 8 bytes, little endian]["SOSHOPZ1"].
    /// </summary>
    internal sealed class Payload : IDisposable
    {
        private const string Magic = "SOSHOPZ1";
        private readonly FileStream file;

        private Payload(FileStream file, Stream zipStream)
        {
            this.file = file;
            Zip = new ZipArchive(zipStream, ZipArchiveMode.Read);
            var versionEntry = Zip.GetEntry("app/version.txt");
            if (versionEntry != null)
            {
                using (var reader = new StreamReader(versionEntry.Open(), Encoding.UTF8)) Version = reader.ReadToEnd().Trim();
            }
            TotalBytes = Zip.Entries.Sum(e => e.Length);
        }

        public ZipArchive Zip { get; }
        public string Version { get; } = "?";
        public long TotalBytes { get; }

        public static Payload Open(string path)
        {
            var file = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.Read, 1 << 16);
            try
            {
                if (path.EndsWith(".zip", StringComparison.OrdinalIgnoreCase)) return new Payload(file, file);
                if (file.Length < 32) throw new InvalidDataException("no payload");
                var trailer = new byte[16];
                file.Seek(-16, SeekOrigin.End);
                ReadExactly(file, trailer);
                if (Encoding.ASCII.GetString(trailer, 8, 8) != Magic) throw new InvalidDataException("no payload");
                long offset = BitConverter.ToInt64(trailer, 0);
                if (offset <= 0 || offset >= file.Length - 16) throw new InvalidDataException("bad payload offset");
                return new Payload(file, new SubStream(file, offset, file.Length - 16 - offset));
            }
            catch
            {
                file.Dispose();
                throw;
            }
        }

        private static void ReadExactly(Stream stream, byte[] buffer)
        {
            int read = 0;
            while (read < buffer.Length)
            {
                int n = stream.Read(buffer, read, buffer.Length - read);
                if (n == 0) throw new EndOfStreamException();
                read += n;
            }
        }

        public void Dispose()
        {
            Zip.Dispose();
            file.Dispose();
        }
    }

    /// <summary>A read-only window onto part of a file, so ZipArchive can read the appended zip.</summary>
    internal sealed class SubStream : Stream
    {
        private readonly Stream inner;
        private readonly long start;
        private readonly long length;
        private long position;

        public SubStream(Stream inner, long start, long length)
        {
            this.inner = inner;
            this.start = start;
            this.length = length;
        }

        public override bool CanRead => true;
        public override bool CanSeek => true;
        public override bool CanWrite => false;
        public override long Length => length;

        public override long Position
        {
            get => position;
            set => position = value;
        }

        public override int Read(byte[] buffer, int offset, int count)
        {
            if (position >= length) return 0;
            inner.Position = start + position;
            int n = inner.Read(buffer, offset, (int)Math.Min(count, length - position));
            position += n;
            return n;
        }

        public override long Seek(long offset, SeekOrigin origin)
        {
            switch (origin)
            {
                case SeekOrigin.Begin: position = offset; break;
                case SeekOrigin.Current: position += offset; break;
                default: position = length + offset; break;
            }
            return position;
        }

        public override void Flush() { }
        public override void SetLength(long value) => throw new NotSupportedException();
        public override void Write(byte[] buffer, int offset, int count) => throw new NotSupportedException();
    }

    /// <summary>Copies the app into the install folder and registers it with Windows.</summary>
    internal sealed class Installer
    {
        private readonly Payload payload;
        private readonly string dir;

        public Installer(Payload payload, string dir)
        {
            this.payload = payload;
            this.dir = Path.GetFullPath(dir).TrimEnd('\\');
        }

        public bool StartMenu { get; set; } = true;
        public bool Desktop { get; set; } = true;
        public bool Register { get; set; } = true;
        public string Dir => dir;

        public void Install(Action<int, string> progress)
        {
            if (!InstallInfo.IsAscii(dir))
                throw new SetupError("Thư mục cài đặt chỉ được dùng chữ không dấu (ví dụ C:\\SoShop).");
            if (InstallInfo.IsLauncherRunning())
                throw new SetupError("SoShop đang chạy. Hãy đóng cửa sổ SoShop rồi cài lại.");

            Directory.CreateDirectory(dir);
            var app = Path.Combine(dir, "app");
            if (Directory.Exists(app))
            {
                progress(0, "Đang gỡ bản cũ...");
                DeleteWithRetry(app);
            }

            var root = dir + "\\";
            var buffer = new byte[1 << 20];
            long total = Math.Max(1, payload.TotalBytes), done = 0;
            int lastPercent = -1;
            var lastReport = DateTime.MinValue;
            foreach (var entry in payload.Zip.Entries)
            {
                var name = entry.FullName.Replace('/', '\\');
                var target = Path.GetFullPath(Path.Combine(root, name));
                if (!target.StartsWith(root, StringComparison.OrdinalIgnoreCase)) continue;
                if (name.EndsWith("\\"))
                {
                    Directory.CreateDirectory(target);
                    continue;
                }
                Directory.CreateDirectory(Path.GetDirectoryName(target));
                using (var source = entry.Open())
                using (var output = new FileStream(target, FileMode.Create, FileAccess.Write, FileShare.None, 1 << 16))
                {
                    int n;
                    while ((n = source.Read(buffer, 0, buffer.Length)) > 0)
                    {
                        output.Write(buffer, 0, n);
                        done += n;
                    }
                }
                int percent = (int)(done * 100 / total);
                if (percent != lastPercent && DateTime.Now - lastReport > TimeSpan.FromMilliseconds(150))
                {
                    lastPercent = percent;
                    lastReport = DateTime.Now;
                    progress(percent, $"Đang chép tệp... {percent}%");
                }
            }

            progress(100, "Đang tạo biểu tượng...");
            var exe = Path.Combine(dir, InstallInfo.LauncherExe);
            if (Register) InstallInfo.WriteUninstallEntry(dir, payload.Version, payload.TotalBytes / 1024);
            if (StartMenu) InstallInfo.CreateShortcut(InstallInfo.StartMenuShortcut, exe, "SoShop: live bán hàng với AI");
            if (Desktop) InstallInfo.CreateShortcut(InstallInfo.DesktopShortcut, exe, "SoShop: live bán hàng với AI");
            progress(100, "Đã cài xong.");
        }

        private static void DeleteWithRetry(string path)
        {
            for (int attempt = 1; ; attempt++)
            {
                try
                {
                    Directory.Delete(path, true);
                    return;
                }
                catch (Exception e) when (e is IOException || e is UnauthorizedAccessException)
                {
                    if (attempt >= 5)
                        throw new SetupError("Không xóa được bản cũ (có tệp đang được dùng). Hãy khởi động lại máy rồi cài lại.");
                    Thread.Sleep(1500);
                }
            }
        }
    }

    internal sealed class SetupForm : Form
    {
        private readonly Payload payload;
        private readonly bool shortcuts;
        private readonly bool registry;
        private readonly TextBox folder;
        private readonly Button browse;
        private readonly CheckBox desktop;
        private readonly ProgressBar bar;
        private readonly Label status;
        private readonly Button primary;
        private readonly Button secondary;
        private bool busy;
        private bool installed;
        private string installedDir;

        public SetupForm(Payload payload, string dir, bool shortcuts, bool registry)
        {
            this.payload = payload;
            this.shortcuts = shortcuts;
            this.registry = registry;
            Text = "Cài đặt SoShop";
            Icon = Icon.ExtractAssociatedIcon(Application.ExecutablePath);
            Font = Brand.Font(9.5f);
            BackColor = Color.White;
            FormBorderStyle = FormBorderStyle.FixedSingle;
            MaximizeBox = false;
            StartPosition = FormStartPosition.CenterScreen;
            AutoScaleDimensions = new SizeF(96f, 96f);
            AutoScaleMode = AutoScaleMode.Dpi;
            ClientSize = new Size(520, 420);

            SuspendLayout();
            var body = new TableLayoutPanel
            {
                Dock = DockStyle.Fill,
                Padding = new Padding(24, 18, 24, 16),
                ColumnCount = 1,
                GrowStyle = TableLayoutPanelGrowStyle.AddRows,
            };
            body.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 100));

            var intro = new Label
            {
                Text = "SoShop sẽ được cài cho tài khoản Windows này. Không cần quyền quản trị, không cần cài thêm " +
                       "Docker, Python hay cơ sở dữ liệu: mọi thứ đã có sẵn trong bộ cài.",
                AutoSize = true,
                MaximumSize = new Size(470, 0),
                ForeColor = Brand.Ink,
                Margin = new Padding(0, 0, 0, 14),
            };

            var caption = new Label
            {
                Text = "Thư mục cài đặt",
                AutoSize = true,
                Font = Brand.Font(9.5f, FontStyle.Bold),
                ForeColor = Brand.Ink,
                Margin = new Padding(0, 0, 0, 4),
            };
            folder = new TextBox { Text = dir, Width = 370, Margin = new Padding(0) };
            browse = Brand.Secondary("Đổi...");
            browse.Size = new Size(90, 27);
            browse.Margin = new Padding(8, 0, 0, 0);
            browse.Click += (s, e) => Browse();
            var folderRow = new FlowLayoutPanel { AutoSize = true, WrapContents = false, Margin = new Padding(0, 0, 0, 10) };
            folderRow.Controls.Add(folder);
            folderRow.Controls.Add(browse);

            desktop = new CheckBox
            {
                Text = "Tạo biểu tượng SoShop trên màn hình Desktop",
                Checked = shortcuts,
                Enabled = shortcuts,
                AutoSize = true,
                ForeColor = Brand.Ink,
                Margin = new Padding(0, 0, 0, 8),
            };

            var size = new Label
            {
                Text = $"Phiên bản {payload.Version}. Cần khoảng {Math.Ceiling(payload.TotalBytes / 1e9 + 1)} GB ổ đĩa trống " +
                       "(thêm chỗ cho video bạn tạo).",
                AutoSize = true,
                MaximumSize = new Size(470, 0),
                ForeColor = Brand.Muted,
                Margin = new Padding(0, 0, 0, 14),
            };

            bar = new ProgressBar { Dock = DockStyle.Top, Height = 10, Maximum = 100, Margin = new Padding(0, 0, 0, 6) };
            status = new Label { AutoSize = true, MaximumSize = new Size(470, 0), ForeColor = Brand.Ink, Margin = new Padding(0, 0, 0, 0) };

            var buttons = new FlowLayoutPanel
            {
                FlowDirection = FlowDirection.RightToLeft,
                Dock = DockStyle.Fill,
                AutoSize = true,
                Margin = new Padding(0, 16, 0, 0),
            };
            primary = Brand.Primary("Cài đặt");
            primary.Size = new Size(150, 42);
            primary.Click += (s, e) => OnPrimary();
            secondary = Brand.Secondary("Hủy");
            secondary.Size = new Size(110, 42);
            secondary.Margin = new Padding(0, 0, 10, 0);
            secondary.Click += (s, e) => Close();
            buttons.Controls.Add(primary);
            buttons.Controls.Add(secondary);

            foreach (var control in new Control[] { intro, caption, folderRow, desktop, size, bar, status, buttons })
            {
                body.RowStyles.Add(new RowStyle(SizeType.AutoSize));
                body.Controls.Add(control);
            }
            Controls.Add(body);
            Controls.Add(new BrandHeader("Cài đặt SoShop", "Live bán hàng với AI   ·   bản " + payload.Version));
            ResumeLayout(true);
            AcceptButton = primary;
        }

        private void Browse()
        {
            using (var dialog = new FolderBrowserDialog { Description = "Chọn nơi cài SoShop", SelectedPath = folder.Text })
            {
                if (dialog.ShowDialog(this) != DialogResult.OK) return;
                var chosen = dialog.SelectedPath.TrimEnd('\\');
                // Always install into a folder of its own: uninstalling removes it.
                folder.Text = Path.GetFileName(chosen).Equals("SoShop", StringComparison.OrdinalIgnoreCase)
                    ? chosen
                    : Path.Combine(chosen, "SoShop");
            }
        }

        private void OnPrimary()
        {
            if (installed)
            {
                Process.Start(Path.Combine(installedDir, InstallInfo.LauncherExe))?.Dispose();
                Close();
                return;
            }
            var dir = folder.Text.Trim();
            try
            {
                dir = Path.GetFullPath(dir);
            }
            catch (Exception e) when (e is ArgumentException || e is NotSupportedException || e is PathTooLongException)
            {
                MessageBox.Show(this, "Thư mục cài đặt không hợp lệ.", Text, MessageBoxButtons.OK, MessageBoxIcon.Warning);
                return;
            }
            if (!InstallInfo.IsAscii(dir))
            {
                MessageBox.Show(this, "Thư mục cài đặt chỉ được dùng chữ không dấu, ví dụ C:\\SoShop.", Text,
                    MessageBoxButtons.OK, MessageBoxIcon.Warning);
                return;
            }
            var free = new DriveInfo(Path.GetPathRoot(dir)).AvailableFreeSpace;
            if (free < payload.TotalBytes + 500_000_000L)
            {
                MessageBox.Show(this, $"Ổ đĩa {Path.GetPathRoot(dir)} không đủ chỗ trống. Cần khoảng " +
                                      $"{Math.Ceiling(payload.TotalBytes / 1e9 + 1)} GB.", Text,
                    MessageBoxButtons.OK, MessageBoxIcon.Warning);
                return;
            }
            while (InstallInfo.IsLauncherRunning())
            {
                if (MessageBox.Show(this, "SoShop đang chạy. Hãy đóng cửa sổ SoShop rồi bấm Retry.", Text,
                        MessageBoxButtons.RetryCancel, MessageBoxIcon.Information) != DialogResult.Retry) return;
            }

            var installer = new Installer(payload, dir) { StartMenu = shortcuts, Desktop = desktop.Checked, Register = registry };
            busy = true;
            folder.Enabled = browse.Enabled = desktop.Enabled = primary.Enabled = secondary.Enabled = false;
            status.Text = "Đang chuẩn bị...";
            Task.Run(() => installer.Install((percent, message) => BeginInvoke(new Action(() =>
                {
                    bar.Value = Math.Min(100, Math.Max(0, percent));
                    status.Text = message;
                }))))
                .ContinueWith(task => BeginInvoke(new Action(() => Finished(task, installer.Dir))));
        }

        private void Finished(Task task, string dir)
        {
            busy = false;
            if (task.Exception != null)
            {
                var error = task.Exception.GetBaseException();
                status.Text = error is SetupError ? error.Message : "Cài đặt thất bại: " + error.Message;
                MessageBox.Show(this, status.Text, Text, MessageBoxButtons.OK, MessageBoxIcon.Error);
                folder.Enabled = browse.Enabled = primary.Enabled = secondary.Enabled = true;
                desktop.Enabled = shortcuts;
                return;
            }
            installed = true;
            installedDir = dir;
            bar.Value = 100;
            status.Text = "Đã cài xong SoShop. Bấm \"Mở SoShop\" để bắt đầu. Lần sau mở SoShop từ biểu tượng trên " +
                          "Desktop hoặc trong menu Start.";
            primary.Text = "Mở SoShop";
            secondary.Text = "Đóng";
            primary.Enabled = secondary.Enabled = true;
            primary.Focus();
        }

        protected override void OnFormClosing(FormClosingEventArgs e)
        {
            if (busy && e.CloseReason == CloseReason.UserClosing)
            {
                e.Cancel = true;
                return;
            }
            base.OnFormClosing(e);
        }
    }
}
