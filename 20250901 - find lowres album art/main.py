import os
import sys
from PIL import Image

def find_low_res_images(start_path, output_file):
    """
    Traverses a directory tree to find specific image files with low resolution.

    Args:
        start_path (str): The starting directory for the search.
        output_file (str): The name of the file to write the paths to.
    """
    # Define the target image filenames and the minimum resolution
    target_filenames = {"folder.jpg", "cover.jpg", "front.jpg"}
    min_resolution = (800, 800)

    # Get the absolute path for the output file
    output_path = os.path.abspath(output_file)

    # Open the output file in write mode
    with open(output_path, "w") as f_out:
        # Walk through the directory tree
        for dirpath, dirnames, filenames in os.walk(start_path):
            for filename in filenames:
                # Check if the filename is one of our targets
                if filename.lower() in target_filenames:
                    # Construct the full path to the image
                    image_path = os.path.join(dirpath, filename)
                    
                    try:
                        # Open the image file
                        with Image.open(image_path) as img:
                            width, height = img.size
                            # Check if the resolution is less than the minimum
                            if width < min_resolution[0] or height < min_resolution[1]:
                                # If it's low resolution, write its absolute path to the file
                                print(f"Found low-res image: {image_path}")
                                f_out.write(f"{image_path}\n")
                    except Exception as e:
                        # Print an error message if the file can't be opened
                        print(f"Could not process {image_path}: {e}")

if __name__ == "__main__":
    # Check if a command-line argument for the path is provided
    if len(sys.argv) > 1:
        search_directory = sys.argv[1]
    else:
        # Default to the current directory if no path is given
        search_directory = "."
        print("No path provided. Defaulting to the current directory.")

    output_filename = "paths.txt"

    print(f"Starting search for low-resolution images in: {os.path.abspath(search_directory)}")
    print("This may take some time depending on the size of the directory tree.")
    
    find_low_res_images(search_directory, output_filename)
    
    print(f"\nSearch complete. Low-resolution image paths have been saved to: {os.path.abspath(output_filename)}")
