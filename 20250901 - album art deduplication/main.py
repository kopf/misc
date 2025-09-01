import os
from PIL import Image

def deduplicate_images_in_directory(directory_path, filenames):
    """
    Traverses a directory and, for each subdirectory, identifies and removes
    duplicate image files from a given list, keeping only the one with the
    highest resolution.

    Args:
        directory_path (str): The root directory to start the traversal.
        filenames (list): A list of filenames to check for (e.g., ['folder.jpg', 'cover.jpg']).
    """
    if not os.path.isdir(directory_path):
        print(f"Error: Directory not found at {directory_path}")
        return

    print(f"Starting image deduplication in {directory_path}...")

    # Walk through the directory tree
    for root, dirs, files in os.walk(directory_path):
        found_images = []

        # Check for the existence of the specified filenames in the current directory
        for filename in filenames:
            if filename in files:
                file_path = os.path.join(root, filename)
                found_images.append(file_path)

        # Process the found files if there are two or more
        if len(found_images) >= 2:
            print(f"\nFound multiple images in {root}:")
            for img_path in found_images:
                print(f" - {os.path.basename(img_path)}")
            
            highest_res = 0
            best_image_path = None

            # Find the image with the highest resolution
            for img_path in found_images:
                try:
                    with Image.open(img_path) as img:
                        width, height = img.size
                        resolution = width * height
                        if resolution > highest_res:
                            highest_res = resolution
                            best_image_path = img_path
                except Exception as e:
                    print(f"Warning: Could not open {img_path} to check resolution. Skipping. Error: {e}")

            if best_image_path:
                print(f"\nKeeping the highest resolution image: {os.path.basename(best_image_path)}")
                
                # Delete the other, lower-resolution files
                for img_path_to_delete in found_images:
                    if img_path_to_delete != best_image_path:
                        try:
                            os.remove(img_path_to_delete)
                            print(f"Deleted: {os.path.basename(img_path_to_delete)}")
                        except Exception as e:
                            print(f"Error: Could not delete {img_path_to_delete}. Error: {e}")
            else:
                print("\nCould not determine the highest resolution image. No files deleted.")

if __name__ == "__main__":
    # Define the list of filenames to check for
    image_names = ['folder.jpg', 'cover.jpg', 'front.jpg']
    
    # Prompt the user for the root directory
    root_directory = input("Enter the path to the root directory to traverse: ")
    
    # Run the deduplication function
    deduplicate_images_in_directory(root_directory, image_names)

    print("\nScript finished.")

