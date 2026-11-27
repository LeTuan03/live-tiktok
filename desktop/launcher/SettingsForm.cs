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
            env.Set("GROQ_API_KEY", key);
            env.Set("EDGE_TTS_VOICE", voice.Text.Trim().Length > 0 ? voice.Text.Trim() : "google-vi");
            env.Set("ELEVEN_API_KEY", eleven.Text.Trim());
            env.Set("LEMONSLICE_API_KEY", lemon.Text.Trim());
            env.Save();
            DialogResult = DialogResult.OK;
            Close();
        }

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
