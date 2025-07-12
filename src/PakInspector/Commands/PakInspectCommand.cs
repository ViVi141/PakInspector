using PakInspector.Data;
using PakInspector.Parser;
using Spectre.Console;
using System.CommandLine;
using System.Text.Json;

namespace PakInspector.Commands;

internal static class PakInspectCommand
{

    public static Command Command
    {
        get
        {
            var fileArg = new Argument<FileInfo>("file")
            {
                Description = "Path to file"
            };
            fileArg.AcceptExistingOnly();

            var showTreeOption = new Option<bool>("--tree", "-t")
            {
                Description = "Display files as tree"
            };

            var showAllInfoOption = new Option<bool>("--all", "-a")
            {
                Description = "Display all file info"
            };

            var saveOption = new Option<bool>("--save", "-s")
            {
                Description = "Save inspection results"
            };

            var quietOption = new Option<bool>("--quiet", "-q")
            {
                Description = "Do not print to the console"
            };

            var cmd = new Command("inspect", "Inspect contents of the PAC1 file.");
            cmd.Arguments.Add(fileArg);
            cmd.Options.Add(showTreeOption);
            cmd.Options.Add(showAllInfoOption);
            cmd.Options.Add(saveOption);
            cmd.Options.Add(quietOption);

            cmd.SetAction(parseResult => Execute(
                parseResult.GetValue(fileArg),
                parseResult.GetValue(showTreeOption),
                parseResult.GetValue(showAllInfoOption),
                parseResult.GetValue(saveOption),
                parseResult.GetValue(quietOption)
                ));

            return cmd;
        }
    }

    public static int Execute(FileInfo fileInfo,
                              bool showTree,
                              bool showAllInfo,
                              bool saveResults,
                              bool quiet)
    {
        var file = AnsiConsole.Status().Start("Parsing .pak...", ctx => Pak.FromFile(fileInfo.FullName));

        var name = Path.GetFileNameWithoutExtension(fileInfo.FullName);

        if (file.Chunks.First(c => c.TypeId == Pak.Chunk.ChunkType.Head).Body is not Pak.HeadChunk headChunk)
        {
            throw new Exception("Failed to parse head chunk");
        }

        var headContent = Convert.ToBase64String(headChunk.Header);
        if (!quiet)
        {
            AnsiConsole.Write(new Markup($"Pak header:\t[orange1]{headContent}[/]\n\n"));
        }

        if (file.Chunks.First(c => c.TypeId == Pak.Chunk.ChunkType.File).Body is not Pak.FileChunk fileChunk)
        {
            throw new Exception("Failed to parse file chunk");
        }

        var files = AnsiConsole.Status().Start("Parsing file tree...", ctx => PakUtils.GetFiles(fileChunk.Root, "").ToList());
        AnsiConsole.Write(new Markup($"Pak contains [orange1]{files.Count}[/] file(s)\n\n"));

        if (!quiet)
        {
            if (showTree)
            {
                DisplayFileTree(name, fileChunk);
            }
            else
            {
                DisplayFileList(files, showAllInfo);
            }
        }

        if (saveResults)
        {
            AnsiConsole.Status().Start("Saving inspection results...", ctx => SaveReport(name, new(headContent, files)));
        }

        return 0;
    }

    private static void DisplayFileList(IEnumerable<PakFileEntry> files, bool showAllInfo)
    {
        foreach (var f in files)
        {
            var info = showAllInfo ? f.GetInfo() : f.GetShortInfo();
            AnsiConsole.Write(info);
            AnsiConsole.WriteLine();
        }
    }

    private static void DisplayFileTree(string name, Pak.FileChunk fileChunk)
    {
        if (fileChunk.Root.Info is Pak.PakEntry.PakFolderInfo root)
        {
            var tree = new Tree(name);
            foreach (var child in root.Children)
            {
                BuildFileTree(child, tree);
            }
            AnsiConsole.Write(tree);
        }
    }

    private static void BuildFileTree(Pak.PakEntry entry, IHasTreeNodes parent)
    {
        var node = new TreeNode(new Markup(entry.Name));
        var info = entry.Info;
        switch (info)
        {
            case Pak.PakEntry.PakFileInfo:
                break;
            case Pak.PakEntry.PakFolderInfo folder:
                foreach (var child in folder.Children)
                {
                    BuildFileTree(child, node);
                }
                break;
            default:
                break;
        }
        parent.AddNode(node);
    }

    private static void SaveReport(string name, PakReport report)
    {
        using var output = File.Create($"{name}.json");
        JsonSerializer.Serialize(output, report, typeof(PakReport), SourceGenerationContext.Default);
    }

}
