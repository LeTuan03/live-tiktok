using System.Drawing;
using System.Drawing.Drawing2D;
using System.Windows.Forms;

namespace SoShop
{
    /// <summary>
    /// Brand colors and the few styled controls both windows use. Colors come
    /// from web/lib/branding/colors.json.
    /// </summary>
    internal static class Brand
    {
        public static readonly Color Coral = ColorTranslator.FromHtml("#FF4D6D");
        public static readonly Color Magenta = ColorTranslator.FromHtml("#C13C9F");
        public static readonly Color Indigo = ColorTranslator.FromHtml("#5B2FD9");
        public static readonly Color Ink = ColorTranslator.FromHtml("#0E0B1A");
        public static readonly Color Muted = ColorTranslator.FromHtml("#6B6880");
        public static readonly Color Surface = ColorTranslator.FromHtml("#F6F4FB");
        public static readonly Color Line = ColorTranslator.FromHtml("#E4E0EE");
        public static readonly Color Ok = ColorTranslator.FromHtml("#16A34A");
        public static readonly Color Busy = ColorTranslator.FromHtml("#D97706");
        public static readonly Color Bad = ColorTranslator.FromHtml("#DC2626");
        public static readonly Color Idle = ColorTranslator.FromHtml("#9CA3AF");

        public static Font Font(float size, FontStyle style = FontStyle.Regular) =>
            new Font("Segoe UI", size, style, GraphicsUnit.Point);

        public static Button Primary(string text)
        {
            var button = new Button
            {
                Text = text,
                FlatStyle = FlatStyle.Flat,
                BackColor = Magenta,
                ForeColor = Color.White,
                Font = Font(11f, FontStyle.Bold),
                Cursor = Cursors.Hand,
                AutoSize = false,
                UseVisualStyleBackColor = false,
            };
            button.FlatAppearance.BorderSize = 0;
            button.FlatAppearance.MouseOverBackColor = Coral;
            button.FlatAppearance.MouseDownBackColor = Indigo;
            return button;
        }

        public static Button Secondary(string text)
        {
            var button = new Button
            {
                Text = text,
                FlatStyle = FlatStyle.Flat,
                BackColor = Color.White,
                ForeColor = Ink,
                Font = Font(9.5f),
                Cursor = Cursors.Hand,
                AutoSize = false,
                UseVisualStyleBackColor = false,
            };
            button.FlatAppearance.BorderColor = Line;
            button.FlatAppearance.MouseOverBackColor = Surface;
            return button;
        }
    }

    /// <summary>The gradient title band at the top of each window.</summary>
    internal sealed class BrandHeader : Control
    {
        private readonly string title;
        private readonly string subtitle;

        public BrandHeader(string title, string subtitle)
        {
            this.title = title;
            this.subtitle = subtitle;
            DoubleBuffered = true;
            SetStyle(ControlStyles.ResizeRedraw, true);
            Dock = DockStyle.Top;
            Height = 84;
        }

        protected override void OnPaint(PaintEventArgs e)
        {
            var g = e.Graphics;
            var rect = ClientRectangle;
            if (rect.Width <= 0 || rect.Height <= 0) return;
            using (var brush = new LinearGradientBrush(rect, Brand.Coral, Brand.Indigo, 20f))
            {
                brush.InterpolationColors = new ColorBlend
                {
                    Colors = new[] { Brand.Coral, Brand.Magenta, Brand.Indigo },
                    Positions = new[] { 0f, 0.5f, 1f },
                };
                g.FillRectangle(brush, rect);
            }
            g.TextRenderingHint = System.Drawing.Text.TextRenderingHint.ClearTypeGridFit;
            float scale = DeviceDpi / 96f;
            using (var big = Brand.Font(20f, FontStyle.Bold))
            using (var small = Brand.Font(10f))
            {
                var left = (int)(22 * scale);
                TextRenderer.DrawText(g, title, big, new Point(left, (int)(12 * scale)), Color.White,
                    TextFormatFlags.NoPadding);
                TextRenderer.DrawText(g, subtitle, small, new Point(left + 2, (int)(52 * scale)),
                    Color.FromArgb(235, 255, 255, 255), TextFormatFlags.NoPadding);
            }
        }
    }
}
