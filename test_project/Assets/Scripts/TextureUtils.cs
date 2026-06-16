using UnityEngine;

public class TextureUtils : MonoBehaviour
{
    public Texture2D sourceTexture;
    
    void Start()
    {
        // This code uses GetPixels - relevant for Read/Write check
        Color[] pixels = sourceTexture.GetPixels();
        for (int i = 0; i < pixels.Length; i++)
        {
            pixels[i] = Color.white;
        }
        sourceTexture.SetPixels(pixels);
        sourceTexture.Apply();
        
        // Also encode
        byte[] pngData = sourceTexture.EncodeToPNG();
        System.IO.File.WriteAllBytes("output.png", pngData);
    }
}
