using PakInspector.Parser;
using Spectre.Console;
using System.CommandLine;

namespace PakInspector.Commands;

internal static class ChunksCommand
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

            var cmd = new Command("chunks", "List chunks in IFF file.");
            cmd.Arguments.Add(fileArg);
            cmd.SetAction(parseResult => Execute(parseResult.GetValue(fileArg)));

            return cmd;
        }
    }

    public static int Execute(FileInfo fileInfo)
    {
        var file = Iff.FromFile(fileInfo.FullName);

        var table = new Table();
        table.Title($"Form type: {file.FormType}");
        table.AddColumn("TypeId");
        table.AddColumn("Length");

        table.Border(TableBorder.Square);
        foreach (var chunk in file.Chunks)
        {
            table.AddRow(chunk.TypeId, chunk.Length.ToString());
        }
        AnsiConsole.Write(table);
        return 0;
    }
}
