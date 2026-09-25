FactoryBot.define do
  factory :optical_train do
    telescope
    sequence(:key) { |n| "train_#{n}" }
    name { "Esprit 100 + ASI2600MM" }
    camera_name { "ZWO ASI2600MM Pro" }
    camera_type { "mono" }
    focal_length_mm { 550 }
    pixel_size_um { 3.76 }
    sensor_width_px { 6248 }
    sensor_height_px { 4176 }
    has_rotator { true }
    filters { [ { "name" => "Ha", "aliases" => [ "H-alpha", "HA" ] }, { "name" => "OIII", "aliases" => [ "O3" ] }, { "name" => "L", "aliases" => [ "Lum", "Luminance" ] } ] }
  end
end
