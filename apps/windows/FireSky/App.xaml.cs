using System.Text;
using Microsoft.UI.Xaml;

namespace FireSky;

public partial class App : Application
{
    private Window? window;
    public App()
    {
        InitializeComponent();
        // 未处理异常落盘：发布包不带调试器，崩溃诊断需要本机日志。
        // 仅记录已托管的 UI 异常；进程级 failfast 仍走 WER。
        UnhandledException += (_, args) =>
        {
            try
            {
                var log = Path.Combine(
                    Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
                    "FireSky", "crash.log");
                Directory.CreateDirectory(Path.GetDirectoryName(log)!);
                File.AppendAllText(log,
                    $"{DateTimeOffset.Now:yyyy-MM-dd HH:mm:ss.fff} {args.Exception.GetType().FullName}\n"
                    + $"{args.Message}\n{args.Exception.StackTrace}\n\n",
                    new UTF8Encoding(false));
            }
            catch (Exception)
            {
                // 日志写入失败时保留原始异常语义。
            }
            args.Handled = false;
        };
    }
    protected override void OnLaunched(LaunchActivatedEventArgs args)
    {
        window = new MainWindow();
        window.Activate();
    }
}
