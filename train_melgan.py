from tqdm import trange
import tensorflow as tf
import numpy as np

from preprocessing.datasets.audio_dataset import MetadataToDataset, MelGANPreprocessor
from models.melgan.trainer import GANTrainer
from models.melgan.models import Generator, MultiScaleDiscriminator
from utils.config_manager import Config
from utils.logging import SummaryManager
from utils.scripts_utils import dynamic_memory_allocation, basic_train_parser

np.random.seed(42)
tf.random.set_seed(42)

dynamic_memory_allocation()
parser = basic_train_parser()
args = parser.parse_args()

cm = Config(args.config, model_kind='melgan', session_name=args.session_name)
assert cm.config['max_wav_segment_lenght'] % cm.config['hop_length'] == 0, \
    f"Error: max_wav_segment_length ({cm.config['max_wav_segment_lenght']}) must be multiple of hop_length ({cm.config['hop_length']})."

cm.create_remove_dirs(clear_dir=args.clear_dir,
                      clear_logs=args.clear_logs,
                      clear_weights=args.clear_weights)
cm.dump_config()
cm.print_config()
generator = Generator(cm.config['mel_channels'], debug=cm.config['debug'])
discriminator = MultiScaleDiscriminator(debug=cm.config['debug'],
                                        wav_mask_value=-1)  # TODO: define masking values in config
cm.compile_model(generator)
cm.compile_model(discriminator)
trainer = GANTrainer(generator, discriminator, debug=cm.config['debug'])
preprocessor = MelGANPreprocessor(cm.config)
train_data_handler = MetadataToDataset.default_training_from_config(cm.config, preprocessor, max_wav_len=None)
valid_data_handler = MetadataToDataset.default_validation_from_config(cm.config, preprocessor,
                                                               max_wav_len=256 * 100)
train_dataset = train_data_handler.mel_wav_dataset(batch_size=cm.config['batch_size'], shuffle=True)
valid_dataset = valid_data_handler.mel_wav_dataset(batch_size=3, shuffle=False)
summary_manager = SummaryManager(model=generator, log_dir=cm.log_dir, config=cm.config)
checkpoint = tf.train.Checkpoint(step=tf.Variable(1),
                                 gen_optimizer=generator.optimizer,
                                 disc_optimizer=discriminator.optimizer,
                                 disciminator=discriminator,
                                 generator=generator)
manager = tf.train.CheckpointManager(checkpoint, str(cm.weights_dir),
                                     max_to_keep=cm.config['keep_n_weights'],
                                     keep_checkpoint_every_n_hours=cm.config['keep_checkpoint_every_n_hours'])
if manager.latest_checkpoint:
    checkpoint.restore(manager.latest_checkpoint)
    print('Latest checkpoint restored!!')

print('\nTRAINING')
losses = []
test_batch = valid_dataset.next_batch()
t = trange(generator.step, cm.config['max_steps'], leave=True)
for _ in t:
    t.set_description(f'step {generator.step}')
    mel, wav = train_dataset.next_batch()
    # out = trainer.mse_train_step(mel, wav)
    out = trainer.adversarial_train_step(mel, wav)
    
    summary_manager.add_scalars('TrainLosses', out['loss'])
    
    if generator.step % cm.config['train_images_plotting_frequency'] == 0:
        summary_manager.display_plot(tag='TrainPredWav', plot=out['pred_wav'][0])
        summary_manager.display_plot(tag='TrainTargetWav', plot=wav[0])
    
    if (generator.step % cm.config['audio_prediction_frequency'] == 0) or (generator.step == 1):
        summary_manager.add_audio('PredWav', out['pred_wav'], cm.config['sampling_rate'])
        # vmel, vwav = valid_dataset.next_batch()
        vout = generator.forward(test_batch[0])
        summary_manager.add_audio('ValidWav', vout, cm.config['sampling_rate'])
        summary_manager.add_audio('TargetWav', wav, cm.config['sampling_rate'])
    
    if generator.step % cm.config['weights_save_frequency'] == 0:
        save_path = manager.save()
        t.display(f'checkpoint at step {generator.step}: {save_path}', pos=len(cm.config['n_steps_avg_losses']) + 2)
