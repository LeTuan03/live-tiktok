using System;
using System.Diagnostics;
using System.Drawing;
using System.Windows.Forms;

namespace SoShop
{
    /// <summary>The settings most people need, written to soshop.env.</summary>
    internal sealed class SettingsForm : Form
    {
        private readonly EnvFile env = new EnvFile(AppPaths.EnvFile);
        private readonly TextBox groq;
        private readonly ComboBox voice;
        private readonly TextBox eleven;
        private readonly TextBox lemon;
        private readonly TextBox tiktokUrl;
        private readonly TextBox tiktokKey;
        private readonly CheckBox tiktokAuto;

        public SettingsForm(bool firstRun)
        {
            Text = firstRun ? "Chào mừng đến với SoShop" : "Cài đặt SoShop";
            Icon = Icon.ExtractAssociatedIcon(Application.ExecutablePath);
            Font = Brand.Font(9.5f);
            BackColor = Color.White;
            FormBorderStyle = FormBorderStyle.FixedDialog;
            MaximizeBox = false;
            MinimizeBox = false;
            ShowInTaskbar = false;
            StartPosition = FormStartPosition.CenterParent;
            AutoScaleDimensions = new SizeF(96f, 96f);
            AutoScaleMode = AutoScaleMode.Dpi;
            AutoSize = true;
            AutoSizeMode = AutoSizeMode.GrowAndShrink;

            var layout = new TableLayoutPanel
            {
                ColumnCount = 1,
                AutoSize = true,
                Padding = new Padding(22, 18, 22, 16),
                GrowStyle = TableLayoutPanelGrowStyle.AddRows,
            };
            layout.ColumnStyles.Add(new ColumnStyle(SizeType.Absolute, 440));

            if (firstRun)
            {
                layout.Controls.Add(Text_(
                    "SoShop cần một Groq API key (miễn phí) để AI viết kịch bản video và dẫn live. " +
                    "Dán key vào ô dưới đây. Có thể bỏ qua và nhập sau trong Cài đặt.", Brand.Ink, 0, 12));
            }

            layout.Controls.Add(Caption("Groq API key (bắt buộc)"));
            groq = Box(env.Get("GROQ_API_KEY"));
            layout.Controls.Add(groq);
            layout.Controls.Add(Link("Lấy key miễn phí tại console.groq.com/keys", "https://console.groq.com/keys"));

            layout.Controls.Add(Caption("Giọng đọc"));
            voice = new ComboBox
            {
                DropDownStyle = ComboBoxStyle.DropDown,
                Width = 440,
                Margin = new Padding(0, 0, 0, 2),
            };
            voice.Items.AddRange(new object[] { "google-vi", "vi-VN-HoaiMyNeural", "vi-VN-NamMinhNeural", "en-US-AriaNeural" });
            voice.Text = env.Get("EDGE_TTS_VOICE").Length > 0 ? env.Get("EDGE_TTS_VOICE") : "google-vi";
            layout.Controls.Add(voice);
            layout.Controls.Add(Text_("google-vi là giọng tiếng Việt miễn phí, ổn định nhất.", Brand.Muted, 0, 8));

            layout.Controls.Add(Caption("ElevenLabs API key (không bắt buộc, giọng trả phí)"));
            eleven = Box(env.Get("ELEVEN_API_KEY"));
            layout.Controls.Add(eleven);

            layout.Controls.Add(Caption("LemonSlice API key (không bắt buộc, avatar trả phí)"));
            lemon = Box(env.Get("LEMONSLICE_API_KEY"));
            layout.Controls.Add(lemon);

            layout.Controls.Add(Caption("TikTok LIVE (không bắt buộc)"));
            layout.Controls.Add(Text_(
                "Dễ nhất là thêm kênh TikTok ngay trong Studio, trang Kênh phát (đổi key ở đó có tác " +
                "dụng ngay). Hoặc dán Server URL và Stream key lấy trong TikTok LIVE Producer vào đây.",
                Brand.Muted, 0, 4));
            tiktokUrl = Box(env.Get("TIKTOK_SERVER_URL"));
            layout.Controls.Add(Hint("Server URL (rtmp://...)"));
            layout.Controls.Add(tiktokUrl);
            tiktokKey = Box(env.Get("TIKTOK_STREAM_KEY"));
            tiktokKey.UseSystemPasswordChar = true;
            layout.Controls.Add(Hint("Stream key"));
            layout.Controls.Add(tiktokKey);
            tiktokAuto = new CheckBox
            {
                Text = "Bấm Bắt đầu phiên live là tự phát lên TikTok",
                AutoSize = true,
                Checked = !string.Equals(env.Get("TIKTOK_AUTO_ON"), "false", StringComparison.OrdinalIgnoreCase),
                ForeColor = Brand.Ink,
                Margin = new Padding(0, 6, 0, 2),
            };
            layout.Controls.Add(tiktokAuto);
            layout.Controls.Add(Link("Mở TikTok LIVE Producer", "https://livecenter.tiktok.com/producer"));

            var advanced = Link("Mở file cài đặt nâng cao (soshop.env)", null);
            advanced.LinkClicked += (s, e) => Process.Start("notepad.exe", Proc.Quote(AppPaths.EnvFile))?.Dispose();
            layout.Controls.Add(advanced);

            var buttons = new FlowLayoutPanel
            {
                FlowDirection = FlowDirection.RightToLeft,
                AutoSize = true,
                Dock = DockStyle.Fill,
                Margin = new Padding(0, 14, 0, 0),
            };
            var save = Brand.Primary("Lưu");
            save.Size = new Size(110, 38);
            save.Click += (s, e) => Save();
            var cancel = Brand.Secondary(firstRun ? "Để sau" : "Hủy");
            cancel.Size = new Size(110, 38);
            cancel.DialogResult = DialogResult.Cancel;
            cancel.Margin = new Padding(0, 0, 8, 0);
            buttons.Controls.Add(save);
            buttons.Controls.Add(cancel);
            layout.Controls.Add(buttons);

            Controls.Add(layout);
            AcceptButton = save;
            CancelButton = cancel;
        }

        private void Save()
        {
            var key = groq.Text.Trim();
            if (key.Length > 0 && !key.StartsWith("gsk_", StringComparison.Ordinal))
            {
                var answer = MessageBox.Show(this,
                    "Groq key thường bắt đầu bằng \"gsk_\". Key này trông không giống. Vẫn lưu?",
                    "Cài đặt", MessageBoxButtons.YesNo, MessageBoxIcon.Warning);
                if (answer != DialogResult.Yes) return;
            }
            var url = tiktokUrl.Text.Trim();
            var streamKey = tiktokKey.Text.Trim();
            if (!CheckTikTok(url, streamKey)) return;
            // Read the file again: it may have been edited in Notepad (the
            // advanced-settings link) while this dialog was open.
            var latest = new EnvFile(AppPaths.EnvFile);
            latest.Set("GROQ_API_KEY", key);
            latest.Set("EDGE_TTS_VOICE", voice.Text.Trim().Length > 0 ? voice.Text.Trim() : "google-vi");
            latest.Set("ELEVEN_API_KEY", eleven.Text.Trim());
            latest.Set("LEMONSLICE_API_KEY", lemon.Text.Trim());
            // TikTok is set up in the Studio (Kênh phát) by default: only write
            // these when used here, so a fresh soshop.env gets no empty lines.
            if (url.Length > 0 || latest.Has("TIKTOK_SERVER_URL"))
            {
                latest.Set("TIKTOK_SERVER_URL", url);
                latest.Set("TIKTOK_STREAM_KEY", streamKey);
                latest.Set("TIKTOK_AUTO_ON", tiktokAuto.Checked ? "true" : "false");
            }
            latest.Save();
            DialogResult = DialogResult.OK;
            Close();
        }

        /// <summary>The same rules the server applies (soshop.restream.platforms), said up front.</summary>
        private bool CheckTikTok(string url, string streamKey)
        {
            string problem = null;
            if (url.Length == 0 && streamKey.Length > 0)
                problem = "Đã có Stream key nhưng thiếu Server URL của TikTok.";
            else if (url.Length > 0 && !url.StartsWith("rtmp://", StringComparison.OrdinalIgnoreCase)
                     && !url.StartsWith("rtmps://", StringComparison.OrdinalIgnoreCase))
                problem = "Server URL của TikTok phải bắt đầu bằng rtmp:// hoặc rtmps://.";
            else if (url.IndexOf(' ') >= 0 || streamKey.IndexOf(' ') >= 0)
                problem = "Server URL và Stream key không được chứa khoảng trắng.";
            if (problem != null)
            {
                MessageBox.Show(this, problem, "Cài đặt", MessageBoxButtons.OK, MessageBoxIcon.Warning);
                return false;
            }
            if (url.Length > 0 && streamKey.Length == 0)
            {
                var answer = MessageBox.Show(this,
                    "Chưa có Stream key nên TikTok sẽ chưa nhận được hình. Vẫn lưu?",
                    "Cài đặt", MessageBoxButtons.YesNo, MessageBoxIcon.Warning);
                return answer == DialogResult.Yes;
            }
            return true;
        }

        private static Label Hint(string text) => new Label
        {
            Text = text,
            AutoSize = true,
            ForeColor = Brand.Muted,
            Margin = new Padding(0, 4, 0, 2),
        };

        private static Label Caption(string text) => new Label
        {
            Text = text,
            AutoSize = true,
            ForeColor = Brand.Ink,
            Font = Brand.Font(9.5f, FontStyle.Bold),
            Margin = new Padding(0, 8, 0, 4),
        };

        private static Label Text_(string text, Color color, int top, int bottom) => new Label
        {
            Text = text,
            AutoSize = true,
            MaximumSize = new Size(440, 0),
            ForeColor = color,
            Margin = new Padding(0, top, 0, bottom),
        };

        private static TextBox Box(string value) => new TextBox
        {
            Text = value,
            Width = 440,
            Margin = new Padding(0, 0, 0, 2),
        };

        private static LinkLabel Link(string text, string url)
        {
            var link = new LinkLabel
            {
                Text = text,
                AutoSize = true,
                LinkColor = Brand.Indigo,
                ActiveLinkColor = Brand.Magenta,
                Margin = new Padding(0, 2, 0, 6),
            };
            if (url != null) link.LinkClicked += (s, e) => Stack.OpenUrl(url);
            return link;
        }
    }
}
