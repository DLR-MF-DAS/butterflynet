import tensorflow as tf
from tensorflow.keras.layers import *
from tensorflow.keras.models import Model
from tensorflow.keras.applications import *
from experimental.vit import *

def squeeze_excite_block(inputs, ratio=8):
    init = inputs
    channel_axis = -1
    filters = init.shape[channel_axis]
    se_shape = (1, 1, filters)

    se = GlobalAveragePooling2D()(init)
    se = Reshape(se_shape)(se)
    se = Dense(filters // ratio, activation='relu', kernel_initializer='he_normal', use_bias=False)(se)
    se = Dense(filters, activation='sigmoid', kernel_initializer='he_normal', use_bias=False)(se)

    x = Multiply()([init, se])
    return x

def conv_block(inputs, filters):
    x = inputs

    x = Conv2D(filters, (3, 3), padding="same")(x)
    x = BatchNormalization()(x)
    x = Activation('relu')(x)

    x = Conv2D(filters, (3, 3), padding="same")(x)
    x = BatchNormalization()(x)
    x = Activation('relu')(x)

    x = squeeze_excite_block(x)

    return x

def encoder1(inputs):
    skip_connections = []

    model = VGG19(include_top=False, weights=None, input_tensor=inputs)
    names = ["block1_conv2", "block2_conv2", "block3_conv4", "block4_conv4"]
    for name in names:
        skip_connections.append(model.get_layer(name).output)

    output = model.get_layer("block5_conv4").output
    return output, skip_connections

def decoder1(inputs, skip_connections):
    num_filters = [256, 128, 64, 32]
    skip_connections.reverse()
    x = inputs

    for i, f in enumerate(num_filters):
        x = UpSampling2D((2, 2), interpolation='bilinear')(x)
        x = Concatenate()([x, skip_connections[i]])
        x = conv_block(x, f)

    return x

def vit_encoder(inputs, patch_size=8, projection_dim=64):
    # Define constants
    num_patches = (inputs.shape[-2] // patch_size) ** 2
    num_heads = 16
    transformer_units = [projection_dim * 2, projection_dim]
    patches = Patches(patch_size)(inputs)

    # Encode patches.
    encoded_patches = PatchEncoder(num_patches, projection_dim)(patches)

    # Create multiple layers of the Transformer block.
    skip_connections1 = []
    skip_connections2 = []

    num_filters = [32, 64, 128, 256]
    x = inputs

    for i, f in enumerate(num_filters):
        x = conv_block(x, f)
        skip_connections1.append(x)
        x = MaxPool2D((2, 2))(x)

        encoded_patches = transformer_encoder(encoded_patches, num_heads, projection_dim, transformer_units)
        skip_connections2.append(encoded_patches)

        shape = [x.shape[1], x.shape[2], encoded_patches.shape[1] * encoded_patches.shape[2] // (x.shape[1] * x.shape[2])]
        reshaped = tf.keras.layers.Reshape(shape)(encoded_patches)
        x = Concatenate()([x, reshaped])


    encoded_patches = transformer_encoder(encoded_patches, num_heads, projection_dim, transformer_units)

    return x, encoded_patches, skip_connections1, skip_connections2

def vit_decoder(inputs, decoded_patches, skip_1, skip_21, skip_22, projection_dim=64):

        transformer_units = [projection_dim * 2, projection_dim]
        num_heads = 16
        num_filters = [256, 128, 64, 32]
        #skip_1.reverse()
        skip_21.reverse()
        skip_22.reverse()
        x = inputs

        for i, f in enumerate(num_filters):
            decoded_patches = transformer_decoder(decoded_patches, skip_22[i], num_heads, projection_dim,transformer_units)
            shape = [x.shape[1], x.shape[2],decoded_patches.shape[1] * decoded_patches.shape[2] // (x.shape[1] * x.shape[2])]
            reshaped = tf.keras.layers.Reshape(shape)(decoded_patches)
            x = Concatenate()([x, reshaped])
            x = UpSampling2D((2, 2), interpolation='bilinear')(x)
            #x=Conv2DTranspose(f, (2, 2), strides=(2, 2), padding='same')(x)
            x = Concatenate()([x, skip_1[i], skip_21[i]])
            x = conv_block(x, f)

        return x



def encoder2(inputs):
    num_filters = [32, 64, 128, 256]
    skip_connections = []
    x = inputs

    for i, f in enumerate(num_filters):
        x = conv_block(x, f)
        skip_connections.append(x)
        x = MaxPool2D((2, 2))(x)

    return x, skip_connections

def decoder2(inputs, skip_1, skip_2):
    num_filters = [256, 128, 64, 32]
    skip_2.reverse()
    x = inputs

    for i, f in enumerate(num_filters):
        x = UpSampling2D((2, 2), interpolation='bilinear')(x)
        x = Concatenate()([x, skip_1[i], skip_2[i]])
        x = conv_block(x, f)

    return x

def output_block(inputs):
    x = Conv2D(1, (1, 1), padding="same")(inputs)
    x = Activation('sigmoid')(x)
    return x

def Upsample(tensor, size):
    """Bilinear upsampling"""
    def _upsample(x, size):
        return tf.image.resize(images=x, size=size)
    return Lambda(lambda x: _upsample(x, size), output_shape=size)(tensor)

def ASPP(x, filter):
    shape = x.shape

    y1 = AveragePooling2D(pool_size=(shape[1], shape[2]))(x)
    y1 = Conv2D(filter, 1, padding="same")(y1)
    y1 = BatchNormalization()(y1)
    y1 = Activation("relu")(y1)
    y1 = UpSampling2D((shape[1], shape[2]), interpolation='bilinear')(y1)

    y2 = Conv2D(filter, 1, dilation_rate=1, padding="same", use_bias=False)(x)
    y2 = BatchNormalization()(y2)
    y2 = Activation("relu")(y2)

    y3 = Conv2D(filter, 3, dilation_rate=6, padding="same", use_bias=False)(x)
    y3 = BatchNormalization()(y3)
    y3 = Activation("relu")(y3)

    y4 = Conv2D(filter, 3, dilation_rate=12, padding="same", use_bias=False)(x)
    y4 = BatchNormalization()(y4)
    y4 = Activation("relu")(y4)

    y5 = Conv2D(filter, 3, dilation_rate=18, padding="same", use_bias=False)(x)
    y5 = BatchNormalization()(y5)
    y5 = Activation("relu")(y5)

    y = Concatenate()([y1, y2, y3, y4, y5])

    y = Conv2D(filter, 1, dilation_rate=1, padding="same", use_bias=False)(y)
    y = BatchNormalization()(y)
    y = Activation("relu")(y)

    return y

def build_model(shape):
    inputs = Input(shape)
    x, skip_1 = encoder1(inputs)
    x = ASPP(x, 64)
    x = decoder1(x, skip_1)
    outputs1 = output_block(x)

    x = inputs * outputs1

    x, encoded_patches, skip_21, skip_22 = vit_encoder(x)
    x = ASPP(x, 64)
    x = vit_decoder(x, encoded_patches, skip_1, skip_21, skip_22)
    outputs2 = output_block(x)
    #outputs = Concatenate()([outputs1, outputs2])
    #outputs = output_block(outputs)
    model = Model(inputs, outputs2)
    return model

if __name__ == "__main__":
    model = build_model((192, 256, 3))
    model.summary()

