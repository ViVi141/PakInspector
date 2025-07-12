using System.Text.Json.Serialization;

namespace PakInspector.Data;

[JsonSourceGenerationOptions(WriteIndented = true, PropertyNamingPolicy = JsonKnownNamingPolicy.CamelCase)]
[JsonSerializable(typeof(PakReport))]
internal partial class SourceGenerationContext : JsonSerializerContext
{
}