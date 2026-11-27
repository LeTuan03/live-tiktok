using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Text;
using System.Text.RegularExpressions;

namespace SoShop
{
    /// <summary>
    /// soshop.env, the user's settings as KEY=value lines (the same file the
    /// Docker bundle uses). Comments and order are kept when a value changes.
    /// </summary>
    internal sealed class EnvFile
    {
        private static readonly Regex Entry = new Regex(@"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=(.*)$");
        private readonly string path;
        private readonly List<string> lines;

        public EnvFile(string path)
        {
            this.path = path;
            lines = File.Exists(path) ? File.ReadAllLines(path, Encoding.UTF8).ToList() : new List<string>();
        }

        /// <summary>Whether the file sets <paramref name="key"/> at all (a commented-out line does not count).</summary>
        public bool Has(string key)
        {
            foreach (var line in lines)
            {
                var m = Entry.Match(line);
                if (m.Success && m.Groups[1].Value == key) return true;
            }
            return false;
        }

        public string Get(string key)
        {
            foreach (var line in lines)
            {
                var m = Entry.Match(line);
                if (m.Success && m.Groups[1].Value == key) return Clean(m.Groups[2].Value);
            }
            return "";
        }

        public void Set(string key, string value)
        {
            value = (value ?? "").Trim();
            for (int i = 0; i < lines.Count; i++)
            {
                var m = Entry.Match(lines[i]);
                if (m.Success && m.Groups[1].Value == key)
                {
                    lines[i] = key + "=" + value;
                    return;
                }
            }
            lines.Add(key + "=" + value);
        }

        public Dictionary<string, string> All()
        {
            var values = new Dictionary<string, string>();
            foreach (var line in lines)
            {
                var m = Entry.Match(line);
                if (m.Success) values[m.Groups[1].Value] = Clean(m.Groups[2].Value);
            }
            return values;
        }

        public void Save()
        {
            // No BOM: python-dotenv and Docker would read it as part of the first key.
            File.WriteAllLines(path, lines, new UTF8Encoding(false));
        }

        /// <summary>Strips quotes, or an inline " # comment" on an unquoted value, like python-dotenv.</summary>
        private static string Clean(string raw)
        {
            var value = raw.Trim();
            if (value.Length >= 2 && (value[0] == '"' || value[0] == '\'') && value[value.Length - 1] == value[0])
                return value.Substring(1, value.Length - 2);
            var hash = value.IndexOf(" #", System.StringComparison.Ordinal);
            return hash >= 0 ? value.Substring(0, hash).TrimEnd() : value;
        }
    }
}
