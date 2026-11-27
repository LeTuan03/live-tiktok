using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.Threading;
using System.Threading.Tasks;
using System.Windows.Forms;

namespace SoShop
{
    /// <summary>The control window: service status, open the studio, Flow, settings, backup.</summary>
    internal sealed class MainForm : Form
    {
        private readonly Stack stack = new Stack();
        private readonly Dictionary<Service, Label[]> rows = new Dictionary<Service, Label[]>();
        private readonly Button openButton;
        private readonly Button flowButton;
        private readonly Button settingsButton;
        private readonly Button backupButton;
        private readonly Button restoreButton;
        private readonly Button logsButton;
        private readonly Label notice;
        private readonly ProgressBar busyBar;
        private readonly EventWaitHandle showSignal;
        private bool exitAllowed;
        private bool working;
        private bool closing;
        private FlowForm flowForm;

        public MainForm()
        {
            Text = "SoShop";
            Icon = Icon.ExtractAssociatedIcon(Application.ExecutablePath);
            Font = Brand.Font(9.5f);
            BackColor = Color.White;
            FormBorderStyle = FormBorderStyle.FixedSingle;
            MaximizeBox = false;
            StartPosition = FormStartPosition.CenterScreen;
            AutoScaleDimensions = new SizeF(96f, 96f);
            AutoScaleMode = AutoScaleMode.Dpi;
            ClientSize = new Size(500, 610);

            SuspendLayout();
            var body = new TableLayoutPanel
            {
                Dock = DockStyle.Fill,
                Padding = new Padding(24, 16, 24, 12),
                ColumnCount = 1,
                GrowStyle = TableLayoutPanelGrowStyle.AddRows,
            };
            body.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 100));

            var status = new TableLayoutPanel
            {
                ColumnCount = 3,
                Dock = DockStyle.Top,
                AutoSize = true,
                Margin = new Padding(0, 0, 0, 10),
            };
            status.ColumnStyles.Add(new ColumnStyle(SizeType.Absolute, 26));
            status.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 46));
            status.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 54));
            foreach (var service in stack.Services)
            {
                var dot = new Label
                {
                    Text = "●",
                    AutoSize = true,
                    Font = Brand.Font(12f),
                    ForeColor = Brand.Idle,
                    Margin = new Padding(0, 3, 0, 3),
                };
                var name = new Label { Text = service.Title, AutoSize = true, ForeColor = Brand.Ink, Margin = new Padding(0, 6, 0, 4) };
                var detail = new Label { Text = service.Detail, AutoSize = true, ForeColor = Brand.Muted, Margin = new Padding(0, 6, 0, 4) };
                status.Controls.Add(dot);
                status.Controls.Add(name);
                status.Controls.Add(detail);
                rows[service] = new[] { dot, detail };
            }

            openButton = Brand.Primary("Đang khởi động...");
            openButton.Dock = DockStyle.Top;
            openButton.Height = 50;
            openButton.Margin = new Padding(0, 4, 0, 10);
            openButton.Enabled = false;
            openButton.Click += (s, e) => OnOpen();

            flowButton = Brand.Secondary("Kết nối Google Flow");
            flowButton.Click += (s, e) => OnFlow();
            settingsButton = Brand.Secondary("Cài đặt");
            settingsButton.Click += (s, e) => OnSettings(false);
            backupButton = Brand.Secondary("Sao lưu dữ liệu");
            backupButton.Click += (s, e) => OnBackup();
            restoreButton = Brand.Secondary("Khôi phục dữ liệu");
            restoreButton.Click += (s, e) => OnRestore();
            logsButton = Brand.Secondary("Nhật ký");
            logsButton.Click += (s, e) => Explore(AppPaths.LogDir);

            busyBar = new ProgressBar
            {
                Style = ProgressBarStyle.Marquee,
                MarqueeAnimationSpeed = 30,
                Dock = DockStyle.Top,
                Height = 6,
                Visible = false,
                Margin = new Padding(0, 8, 0, 0),
            };
            notice = new Label
            {
                AutoSize = true,
                MaximumSize = new Size(452, 0),
                ForeColor = Brand.Ink,
                Margin = new Padding(0, 8, 0, 6),
                Text = "Đang khởi động SoShop...",
            };
            var footer = new LinkLabel
            {
                AutoSize = true,
                MaximumSize = new Size(452, 0),
                ForeColor = Brand.Muted,
                LinkColor = Brand.Indigo,
                ActiveLinkColor = Brand.Magenta,
                Margin = new Padding(0, 6, 0, 0),
                Font = Brand.Font(8.5f),
            };
            const string folderLink = "Mở thư mục dữ liệu";
            footer.Text = "Đóng cửa sổ này sẽ tắt SoShop. Dữ liệu được giữ lại trong máy. " + folderLink;
            footer.LinkArea = new LinkArea(footer.Text.Length - folderLink.Length, folderLink.Length);
            footer.LinkClicked += (s, e) => Explore(AppPaths.DataDir);

            foreach (var control in new Control[]
                     {
                         status, openButton, Row(flowButton, settingsButton), Row(backupButton, restoreButton, logsButton),
                         busyBar, notice, footer,
                     })
            {
                body.RowStyles.Add(new RowStyle(SizeType.AutoSize));
                body.Controls.Add(control);
            }

            Controls.Add(body);
            Controls.Add(new BrandHeader("SoShop", "Live bán hàng với AI   ·   bản " + AppPaths.Version));
            ResumeLayout(true);

            stack.Changed += () => Ui(RefreshView);
            stack.Notice += message => Ui(() => notice.Text = message);

            showSignal = new EventWaitHandle(false, EventResetMode.AutoReset, InstallInfo.ShowEventName);
            new Thread(WaitForShowSignal) { IsBackground = true, Name = "show-signal" }.Start();
        }

        private static TableLayoutPanel Row(params Button[] buttons)
        {
            var row = new TableLayoutPanel
            {
                ColumnCount = buttons.Length,
                Dock = DockStyle.Top,
                AutoSize = true,
                Margin = new Padding(0, 0, 0, 8),
            };
            for (int i = 0; i < buttons.Length; i++)
            {
                row.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 100f / buttons.Length));
                buttons[i].Dock = DockStyle.Fill;
                buttons[i].Height = 40;
                buttons[i].Margin = new Padding(i == 0 ? 0 : 4, 0, i == buttons.Length - 1 ? 0 : 4, 0);
                row.Controls.Add(buttons[i], i, 0);
            }
            return row;
        }

        protected override void OnShown(EventArgs e)
        {
            base.OnShown(e);
            if (!File.Exists(AppPaths.EnvFile))
            {
                if (File.Exists(AppPaths.EnvTemplate)) File.Copy(AppPaths.EnvTemplate, AppPaths.EnvFile);
                else File.WriteAllText(AppPaths.EnvFile, "");
            }
            if (!Stack.HasGroqKey()) OnSettings(true);
            StartStack();
        }

        private void StartStack()
        {
            notice.Text = "Đang khởi động SoShop...";
            new Thread(stack.Run) { IsBackground = true, Name = "stack" }.Start();
            RefreshView();
        }

        private void RefreshView()
        {
            foreach (var pair in rows)
            {
                pair.Value[0].ForeColor = DotColor(pair.Key.State);
                pair.Value[1].Text = pair.Key.Detail;
            }
            var idle = !working && !closing;
            openButton.Enabled = idle && (stack.Ready || stack.StartFailed);
            openButton.Text = stack.StartFailed ? "Thử khởi động lại" : stack.Ready ? "Mở Studio" : "Đang khởi động...";
            var dbUp = stack.Db.State == State.Running;
            backupButton.Enabled = idle && dbUp;
            restoreButton.Enabled = idle && dbUp;
            settingsButton.Enabled = idle;
            flowButton.Enabled = !closing;
            busyBar.Visible = working || closing;
        }

        private static Color DotColor(State state)
        {
            switch (state)
            {
                case State.Running: return Brand.Ok;
                case State.Starting:
                case State.Waiting: return Brand.Busy;
                case State.Failed: return Brand.Bad;
                default: return Brand.Idle;
            }
        }

        // --- Actions ---------------------------------------------------------------------

        private void OnOpen()
        {
            if (stack.StartFailed)
            {
                RunWork("Đang khởi động lại...", () => stack.Stop(), StartStack);
                return;
            }
            Stack.OpenUrl(Stack.StudioUrl);
        }

        private void OnFlow()
        {
            if (flowForm == null || flowForm.IsDisposed) flowForm = new FlowForm(stack);
            flowForm.Show(this);
            flowForm.Activate();
        }

        private void OnSettings(bool firstRun)
        {
            using (var form = new SettingsForm(firstRun))
            {
                if (form.ShowDialog(this) != DialogResult.OK || firstRun) return;
            }
            if (stack.Ready) RunWork("Đang áp dụng cài đặt mới...", () => stack.RestartPython(), null);
        }

        private void OnBackup()
        {
            using (var dialog = new SaveFileDialog
            {
                Title = "Lưu bản sao lưu SoShop",
                Filter = "Bản sao lưu SoShop (*.zip)|*.zip",
                FileName = $"SoShop-du-lieu-{DateTime.Now:yyyy-MM-dd-HHmm}.zip",
                InitialDirectory = Environment.GetFolderPath(Environment.SpecialFolder.MyDocuments),
            })
            {
                if (dialog.ShowDialog(this) != DialogResult.OK) return;
                var path = dialog.FileName;
                RunWork("Đang sao lưu...", () => DataBackup.Backup(stack, path, Say), () =>
                {
                    notice.Text = "Đã sao lưu: " + path;
                    var answer = MessageBox.Show(this,
                        "Đã sao lưu xong.\n\n" + path +
                        "\n\nChép file này sang máy khác, mở SoShop ở máy đó rồi bấm \"Khôi phục dữ liệu\".\n\nMở thư mục chứa file?",
                        "Sao lưu dữ liệu", MessageBoxButtons.YesNo, MessageBoxIcon.Information);
                    if (answer == DialogResult.Yes)
                        Process.Start("explorer.exe", "/select," + Proc.Quote(path))?.Dispose();
                });
            }
        }

        private void OnRestore()
        {
            using (var dialog = new OpenFileDialog
            {
                Title = "Chọn bản sao lưu SoShop",
                Filter = "Bản sao lưu SoShop (*.zip)|*.zip",
                InitialDirectory = Environment.GetFolderPath(Environment.SpecialFolder.MyDocuments),
            })
            {
                if (dialog.ShowDialog(this) != DialogResult.OK) return;
                var answer = MessageBox.Show(this,
                    "Toàn bộ sản phẩm, video và phiên live hiện có trên máy này sẽ được thay bằng dữ liệu trong bản sao lưu.\n\n" +
                    "Nếu cần giữ dữ liệu hiện tại, hãy bấm Hủy và \"Sao lưu dữ liệu\" trước.\n\nTiếp tục khôi phục?",
                    "Khôi phục dữ liệu", MessageBoxButtons.OKCancel, MessageBoxIcon.Warning, MessageBoxDefaultButton.Button2);
                if (answer != DialogResult.OK) return;
                var path = dialog.FileName;
                RunWork("Đang khôi phục...", () => DataBackup.Restore(stack, path, Say), () =>
                {
                    notice.Text = "Đã khôi phục dữ liệu từ " + Path.GetFileName(path) + ".";
                    MessageBox.Show(this, "Đã khôi phục xong. Bấm \"Mở Studio\" để xem dữ liệu.", "Khôi phục dữ liệu",
                        MessageBoxButtons.OK, MessageBoxIcon.Information);
                });
            }
        }

        /// <summary>Runs slow work off the UI thread with the buttons disabled; errors end up in a message box.</summary>
        private void RunWork(string message, Action work, Action done)
        {
            working = true;
            notice.Text = message;
            RefreshView();
            Task.Run(work).ContinueWith(task => Ui(() =>
            {
                working = false;
                RefreshView();
                if (task.Exception == null)
                {
                    done?.Invoke();
                    return;
                }
                var error = task.Exception.GetBaseException();
                Log.Write("work failed: " + error);
                notice.Text = error is UserError ? error.Message : "Lỗi: " + error.Message;
                MessageBox.Show(this, notice.Text, "SoShop", MessageBoxButtons.OK, MessageBoxIcon.Error);
            }));
        }

        private void Say(string message) => Ui(() => notice.Text = message);

        private static void Explore(string folder)
        {
            Directory.CreateDirectory(folder);
            Process.Start("explorer.exe", Proc.Quote(folder))?.Dispose();
        }

        // --- Window ----------------------------------------------------------------------

        protected override void OnFormClosing(FormClosingEventArgs e)
        {
            if (exitAllowed)
            {
                base.OnFormClosing(e);
                return;
            }
            if (e.CloseReason == CloseReason.WindowsShutDown || e.CloseReason == CloseReason.TaskManagerClosing)
            {
                stack.Stop();
                base.OnFormClosing(e);
                return;
            }
            e.Cancel = true;
            if (closing) return;
            if (working)
            {
                MessageBox.Show(this, "SoShop đang sao lưu hoặc khôi phục dữ liệu. Hãy chờ xong rồi tắt.", "SoShop",
                    MessageBoxButtons.OK, MessageBoxIcon.Information);
                return;
            }
            var answer = MessageBox.Show(this,
                "Tắt SoShop?\n\nStudio, màn hình phát live và việc tạo video bằng Google Flow sẽ dừng lại. Dữ liệu vẫn được giữ.",
                "Tắt SoShop", MessageBoxButtons.YesNo, MessageBoxIcon.Question);
            if (answer != DialogResult.Yes) return;
            closing = true;
            notice.Text = "Đang tắt SoShop...";
            RefreshView();
            Task.Run(() => stack.Stop()).ContinueWith(_ => Ui(() =>
            {
                exitAllowed = true;
                Close();
            }));
        }

        protected override void OnFormClosed(FormClosedEventArgs e)
        {
            showSignal.Dispose();
            base.OnFormClosed(e);
        }

        private void WaitForShowSignal()
        {
            try
            {
                while (showSignal.WaitOne())
                {
                    Ui(() =>
                    {
                        if (WindowState == FormWindowState.Minimized) WindowState = FormWindowState.Normal;
                        Activate();
                        if (stack.Ready) Stack.OpenUrl(Stack.StudioUrl);
                    });
                }
            }
            catch (ObjectDisposedException)
            {
                // The window closed.
            }
        }

        private void Ui(Action action)
        {
            if (IsDisposed || !IsHandleCreated) return;
            try
            {
                BeginInvoke(action);
            }
            catch (InvalidOperationException)
            {
                // Closing.
            }
        }
    }
}
