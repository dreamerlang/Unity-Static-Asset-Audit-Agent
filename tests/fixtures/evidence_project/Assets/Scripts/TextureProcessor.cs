using UnityEngine;

/// <summary>
/// Processes linked_texture for runtime effects.
/// This script directly references linked_texture.
/// </summary>
public class TextureProcessor : MonoBehaviour
{
    public Texture2D linkedTexture;  // assigned in editor to linked_texture

    void Start()
    {
        // Read pixels from the linked texture for post-processing
        Color[] pixels = linkedTexture.GetPixels();

        // Comment mentioning GetPixels - should not count as API call
        // var x = anotherTexture.GetPixels();

        for (int i = 0; i < pixels.Length; i++)
        {
            pixels[i] = Color.white;
        }
        linkedTexture.SetPixels(pixels);
        linkedTexture.Apply();

        // Encode to PNG for save
        byte[] pngData = linkedTexture.EncodeToPNG();
        System.IO.File.WriteAllBytes("output.png", pngData);
    }
}
