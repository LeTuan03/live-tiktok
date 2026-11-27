using System;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.Windows.Forms;
using Microsoft.Win32;

namespace SoShop
{
    /// <summary>
    /// Walks the user through loading the SoShop Flow Bridge extension in
    /// Chrome and shows, live, whether it has connected.
    /// </summary>
    internal sealed class FlowForm : Form
    {
        private const string FlowUrl = "https://flow.google.com/";
        private readonly Stack stack;
        private readonly Label state;
        private readonly Timer timer;

        public FlowForm(Stack stack)
        {
            this.stack = stack;
            Text = "Kết nối Google Flow";
            Icon = Icon.ExtractAssociatedIcon(Application.ExecutablePath);
            Font = Brand.Font(9.5f);
            BackColor = Color.White;
            FormBorderStyle = FormBorderStyle.FixedDialog;
            MaximizeBox = false;
            MinimizeBox = false;
            StartPosition = FormStartPosition.CenterParent;
            AutoScaleDimensions = new SizeF(96f, 96f);
            AutoScaleMode = AutoScaleMode.Dpi;
            AutoSize = true;
            AutoSizeMode = AutoSizeMode.GrowAndShrink;

            var layout = new TableLayoutPanel
            {
                ColumnCount = 2,
                AutoSize = true,
                Padding = new Padding(22, 18, 22, 16),
                GrowStyle = TableLayoutPanelGrowStyle.AddRows,
            };
            layout.ColumnStyles.Add(new ColumnStyle(SizeType.Absolute, 34));
            layout.ColumnStyles.Add(new ColumnStyle(SizeType.Absolute, 440));

            var intro = Paragraph(
                "SoShop tạo video sản phẩm bằng Google Flow ngay trong Chrome của bạn, dùng credit Flow trong " +
                "tài khoản Google của bạn. Mỗi máy chỉ cần làm các bước này một lần.", Brand.Ink);
            layout.Controls.Add(intro);
            layout.SetColumnSpan(intro, 2);

            var openExtensions = Brand.Secondary("Mở trang Tiện ích của Chrome");
            openExtensions.Size = new Size(260, 36);
            openExtensions.Click += (s, e) => OpenExtensionsPage();
            Step(layout, 1, "Mở trang Tiện ích của Chrome (đường dẫn thư mục tiện ích được chép sẵn).", openExtensions);

            Step(layout, 2, "Bật \"Chế độ dành cho nhà phát triển\" (Developer mode) ở góc trên bên phải trang.", null);

            var path = new TextBox { Text = AppPaths.ExtensionDir, ReadOnly = true, Width = 330, BackColor = Brand.Surface };
            var copy = Brand.Secondary("Sao chép");
            copy.Size = new Size(96, 28);
            copy.Margin = new Padding(8, 0, 0, 0);
            copy.Click += (s, e) => CopyPath();
            var pathRow = new FlowLayoutPanel { AutoSize = true, WrapContents = false, Margin = new Padding(0, 4, 0, 0) };
            pathRow.Controls.Add(path);
            pathRow.Controls.Add(copy);
            Step(layout, 3,
                "Bấm \"Tải tiện ích đã giải nén\" (Load unpacked). Trong hộp chọn thư mục, dán đường dẫn sau vào ô " +
                "địa chỉ, bấm Enter rồi bấm \"Chọn thư mục\".", pathRow);

            var openFlow = Brand.Secondary("Mở Google Flow");
            openFlow.Size = new Size(180, 36);
            openFlow.Click += (s, e) => OpenInChrome(FlowUrl);
            Step(layout, 4, "Đăng nhập Google Flow trong Chrome đó. Giữ Chrome mở khi SoShop đang tạo video.", openFlow);

            state = new Label { AutoSize = true, Font = Brand.Font(10.5f, FontStyle.Bold), Margin = new Padding(0, 14, 0, 0) };
            layout.Controls.Add(state);
            layout.SetColumnSpan(state, 2);

            var close = Brand.Primary("Đóng");
            close.Size = new Size(110, 38);
            close.Anchor = AnchorStyles.Right;
            close.Margin = new Padding(0, 14, 0, 0);
            close.Click += (s, e) => Close();
            layout.Controls.Add(close);
            layout.SetColumnSpan(close, 2);

            Controls.Add(layout);
            CancelButton = close;

            timer = new Timer { Interval = 1000 };
            timer.Tick += (s, e) => UpdateState();
            timer.Start();
            UpdateState();
        }

        protected override void OnFormClosed(FormClosedEventArgs e)
        {
            timer.Dispose();
            base.OnFormClosed(e);
        }

        private void UpdateState()
        {
            if (stack.FlowConnected)
            {
                state.ForeColor = Brand.Ok;
                state.Text = stack.FlowTabs > 0
                    ? $"● Đã kết nối. Đang mở {stack.FlowTabs} tab Google Flow."
                    : "● Đã kết nối với Chrome. Có thể tạo video ở trang Video của Studio.";
            }
            else if (stack.Api.State != State.Running)
            {
                state.ForeColor = Brand.Busy;
                state.Text = "● Chờ SoShop khởi động xong...";
            }
            else
            {
                state.ForeColor = Brand.Muted;
                state.Text = "● Chưa thấy tiện ích kết nối. Làm theo các bước trên.";
            }
        }

        private static void Step(TableLayoutPanel layout, int number, string text, Control extra)
        {
            var badge = new Label
            {
                Text = number.ToString(),
                Size = new Size(24, 24),
                TextAlign = ContentAlignment.MiddleCenter,
                BackColor = Brand.Magenta,
                ForeColor = Color.White,
                Font = Brand.Font(9.5f, FontStyle.Bold),
                Margin = new Padding(0, 12, 8, 0),
            };
            var column = new FlowLayoutPanel
            {
                FlowDirection = FlowDirection.TopDown,
                AutoSize = true,
                WrapContents = false,
                Margin = new Padding(0, 12, 0, 0),
            };
            column.Controls.Add(Paragraph(text, Brand.Ink));
            if (extra != null)
            {
                extra.Margin = new Padding(0, 6, 0, 0);
                column.Controls.Add(extra);
            }
            layout.Controls.Add(badge);
            layout.Controls.Add(column);
        }

        private static Label Paragraph(string text, Color color) => new Label
        {
            Text = text,
            AutoSize = true,
            MaximumSize = new Size(440, 0),
            ForeColor = color,
            Margin = new Padding(0),
        };

        private void CopyPath()
        {
            try
            {
                Clipboard.SetText(AppPaths.ExtensionDir);
            }
            catch (System.Runtime.InteropServices.ExternalException)
            {
                // Clipboard busy; the path is visible in the box anyway.
            }
        }

        private void OpenExtensionsPage()
        {
            CopyPath();
            if (!OpenInChrome("chrome://extensions/"))
            {
                MessageBox.Show(this,
                    "Không tìm thấy Google Chrome trên máy này. Hãy cài Chrome rồi làm lại bước này.",
                    "Kết nối Google Flow", MessageBoxButtons.OK, MessageBoxIcon.Warning);
            }
        }

        private static bool OpenInChrome(string url)
        {
            var chrome = ChromePath();
            if (chrome == null)
            {
                if (url.StartsWith("http", StringComparison.Ordinal)) Stack.OpenUrl(url);
                return false;
            }
            Process.Start(chrome, url)?.Dispose();
            return true;
        }

        private static string ChromePath()
        {
            const string appPath = @"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\chrome.exe";
            foreach (var root in new[] { Registry.CurrentUser, Registry.LocalMachine })
            {
                using (var key = root.OpenSubKey(appPath))
                {
                    if (key?.GetValue("") is string path && File.Exists(path)) return path;
                }
            }
            foreach (var folder in new[]
                     {
                         Environment.GetFolderPath(Environment.SpecialFolder.ProgramFiles),
                         Environment.GetFolderPath(Environment.SpecialFolder.ProgramFilesX86),
                         Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
                     })
            {
                var path = Path.Combine(folder, "Google", "Chrome", "Application", "chrome.exe");
                if (File.Exists(path)) return path;
            }
            return null;
        }
    }
}
